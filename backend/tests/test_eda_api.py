import copy
import hashlib
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pyarrow as pa
import pyarrow.parquet as pq

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.database import Base
from app.models import Dataset, DatasetFile, EDAAnalysis, Project, User
from app.migrations.versions import b27c49e1d8f3_add_eda_analyses_table as eda_migration
from app import main, normalization, eda


class MissingObject(Exception):
    def __init__(self, code: str):
        self.code = code


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def bucket_exists(self, _bucket):
        return any(bucket == _bucket for bucket, _key in self.objects)

    def make_bucket(self, _bucket):
        return None

    def stat_object(self, bucket, key):
        try:
            stored = self.objects[(bucket, key)]
        except KeyError:
            raise MissingObject("NoSuchKey") from None
        return SimpleNamespace(
            size=len(stored["content"]),
            metadata=(
                {"x-amz-meta-sha256": stored["metadata"]["sha256"]}
                if "sha256" in stored["metadata"]
                else {}
            ),
        )

    def get_object(self, bucket, key):
        try:
            return BytesIO(self.objects[(bucket, key)]["content"])
        except KeyError:
            raise MissingObject("NoSuchKey") from None

    def put_object(
        self,
        bucket,
        key,
        stream,
        _size,
        content_type=None,
        metadata=None,
    ):
        self.objects[(bucket, key)] = {
            "content": stream.read(),
            "content_type": content_type,
            "metadata": metadata or {},
        }

    def remove_object(self, bucket, key):
        self.objects.pop((bucket, key), None)

    def list_objects(self, bucket, prefix, recursive):
        del recursive
        return [
            SimpleNamespace(object_name=key)
            for object_bucket, key in self.objects
            if object_bucket == bucket and key.startswith(prefix)
        ]

    def remove_objects(self, bucket, objects):
        for obj in objects:
            self.remove_object(bucket, obj.name)
        return []


class EDAApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        session = self.session_factory()
        user = User(email="eda-test@example.invalid")
        session.add(user)
        session.flush()
        project = Project(name="EDA Test Project", user_id=user.id)
        session.add(project)
        session.flush()
        dataset = Dataset(
            name="EDA Test Dataset",
            project_id=project.id,
            status="Created",
        )
        session.add(dataset)
        session.commit()
        self.project_id = project.id
        self.dataset_id = dataset.id
        session.close()

        self.storage = FakeStorage()
        self.storage.make_bucket("insightos-raw")
        self.storage.make_bucket("insightos-processed")

        self.held_locks = set()

        def acquire_upload_lock(session, connection, dataset_id, key):
            self.assertIs(session.get_bind(), connection)
            lock_name = f"{dataset_id}:{key}"
            if lock_name in self.held_locks:
                return None
            self.held_locks.add(lock_name)
            return lock_name

        def release_upload_lock(session, connection, lock_id):
            self.assertIs(session.get_bind(), connection)
            self.held_locks.discard(lock_id)

        self.client_patches = [
            patch.object(main, "SessionLocal", self.session_factory),
            patch.object(
                main,
                "_try_acquire_upload_lock",
                side_effect=acquire_upload_lock,
            ),
            patch.object(
                main,
                "_release_upload_lock",
                side_effect=release_upload_lock,
            ),
            patch.object(main, "get_storage_client", return_value=self.storage),
            patch.object(main, "S3Error", MissingObject),
            patch.object(normalization, "S3Error", MissingObject),
            patch.object(eda, "S3Error", MissingObject),
            patch.dict(
                "os.environ",
                {
                    "MINIO_RAW_BUCKET": "insightos-raw",
                    "MINIO_PROCESSED_BUCKET": "insightos-processed",
                },
            ),
        ]
        for item in self.client_patches:
            item.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for item in reversed(self.client_patches):
            item.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _setup_normalized_file(self):
        table = pa.table(
            {
                "a": pa.array([1, 2, 3, 4], type=pa.int64()),
                "b": pa.array([10.5, 20.0, 30.5, 40.0], type=pa.float64()),
                "c": pa.array(["x", "y", "x", "z"], type=pa.string()),
            }
        )
        buf = BytesIO()
        pq.write_table(table, buf)
        parquet_bytes = buf.getvalue()

        upload = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("test.parquet", parquet_bytes, "application/vnd.apache.parquet")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        file_id = upload.json()["file"]["id"]
        normalized = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/normalize"
        )
        self.assertEqual(normalized.status_code, 200, normalized.text)
        return file_id

    def test_univariate_eda_success_and_persistence(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={
                "analysis_type": "univariate",
                "selected_columns": ["a", "b", "c"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["dataset_id"], self.dataset_id)
        self.assertEqual(data["file_id"], file_id)
        self.assertEqual(data["status"], "Completed")
        self.assertIn("id", data)
        analysis_id = data["id"]
        self.assertIsNotNone(data["result"])
        self.assertEqual(data["result"]["analysis_type"], "univariate")
        self.assertEqual(len(data["result"]["columns"]), 3)

        # Check provenance
        provenance = data["provenance"]
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance["verification"]["status"], "verified")
        self.assertTrue(len(provenance["parent_operation_ids"]) > 0)

        # Check database row
        session = self.session_factory()
        record = session.get(EDAAnalysis, analysis_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "Completed")
        self.assertEqual(record.analysis_type, "univariate")
        self.assertIsNone(record.error_code)
        session.close()

        # GET by ID
        get_res = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda/{analysis_id}"
        )
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["id"], analysis_id)
        self.assertEqual(get_res.json()["result"]["columns"][0]["name"], "a")

    def test_bivariate_eda_success(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={
                "analysis_type": "bivariate",
                "selected_columns": ["a", "b"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "Completed")
        self.assertEqual(data["analysis_type"], "bivariate")
        self.assertTrue(len(data["result"]["pair_results"]) > 0)
        pair = data["result"]["pair_results"][0]
        self.assertEqual(pair["left_column"], "a")
        self.assertEqual(pair["right_column"], "b")
        self.assertEqual(pair["pair_type"], "numeric_numeric")
        self.assertIn("pearson_correlation", pair["statistics"])

    def test_multivariate_eda_success(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={
                "analysis_type": "multivariate",
                "selected_columns": ["a", "b"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "Completed")
        self.assertEqual(data["analysis_type"], "multivariate")
        summary = data["result"]["multivariate_summary"]
        self.assertIn("correlation_matrix", summary)
        self.assertEqual(summary["feature_count"], 2)

    def test_request_validation_and_malformed_configuration(self):
        file_id = self._setup_normalized_file()

        # Empty columns
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": []},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"]["code"], "invalid_selected_columns")

        # Unsupported config field
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"], "unsupported_field": True},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"]["code"], "invalid_configuration")

        # Stale aliases (columns, bins, max_serialized_bytes) are rejected
        for stale_payload in (
            {"analysis_type": "univariate", "columns": ["a"]},
            {"analysis_type": "univariate", "selected_columns": ["a"], "bins": 10},
            {"analysis_type": "univariate", "selected_columns": ["a"], "max_serialized_bytes": 100_000},
        ):
            res = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
                json=stale_payload,
            )
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["detail"]["code"], "invalid_configuration")

        # Canonical field names (selected_columns, histogram_bins, max_result_bytes) are accepted
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={
                "analysis_type": "univariate",
                "selected_columns": ["a"],
                "histogram_bins": 15,
                "max_result_bytes": 100_000,
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "Completed")

        # Bivariate with 1 column
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "bivariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 400)

        # Non-existent table index
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"], "table_index": 99},
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"]["code"], "unknown_table")

    def test_dataset_file_mismatch_and_missing_resources(self):
        file_id = self._setup_normalized_file()

        # Missing dataset
        res = self.client.post(
            f"/api/v1/datasets/99999/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 404)

        # Missing file
        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/99999/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 404)

        # File from another dataset
        session = self.session_factory()
        other_dataset = Dataset(name="Other", project_id=self.project_id, status="Created")
        session.add(other_dataset)
        session.commit()
        other_id = other_dataset.id
        session.close()

        res = self.client.post(
            f"/api/v1/datasets/{other_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 404)

        # GET missing analysis_id
        res = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda/99999"
        )
        self.assertEqual(res.status_code, 404)

    def test_normalization_not_ready_conflict(self):
        # File uploaded but NOT normalized
        upload = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("unnormalized.csv", b"a,b\n1,2\n", "text/csv")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        file_id = upload.json()["file"]["id"]

        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"]["code"], "normalization_unavailable")

    def test_concurrency_and_locking_conflicts(self):
        file_id = self._setup_normalized_file()

        # Simulate EDA lock already held
        self.held_locks.add(f"{self.dataset_id}:eda:{file_id}")

        try:
            # 1. Another EDA request on same file conflicts
            res = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
                json={"analysis_type": "univariate", "selected_columns": ["a"]},
            )
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.json()["detail"]["code"], "eda_in_progress")

            # 2. Normalization request on same file conflicts
            res_norm = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/normalize"
            )
            self.assertEqual(res_norm.status_code, 409)
            self.assertEqual(res_norm.json()["detail"]["code"], "eda_in_progress")

            # 3. Dataset deletion conflicts
            res_del = self.client.delete(f"/api/v1/datasets/{self.dataset_id}")
            self.assertEqual(res_del.status_code, 409)
            self.assertEqual(res_del.json()["detail"]["code"], "eda_in_progress")
        finally:
            self.held_locks.discard(f"{self.dataset_id}:eda:{file_id}")

    def test_idempotent_caching_and_recompute(self):
        file_id = self._setup_normalized_file()

        # Run 1
        res1 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res1.status_code, 200)
        id1 = res1.json()["id"]

        # Run 2 with identical configuration returns cached record
        res2 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["id"], id1)

        # Run 3 with recompute=True
        res3 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda?recompute=true",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()["id"], id1)

        # Run 4 with different configuration creates new record
        res4 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["b"]},
        )
        self.assertEqual(res4.status_code, 200)
        self.assertNotEqual(res4.json()["id"], id1)

    def test_pagination_and_listing(self):
        file_id = self._setup_normalized_file()

        # Create two analyses
        self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "bivariate", "selected_columns": ["a", "b"]},
        )

        # Listing
        list_res = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda?limit=10&offset=0"
        )
        self.assertEqual(list_res.status_code, 200)
        body = list_res.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 2)

        # Filter by analysis_type
        filtered = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda?analysis_type=bivariate"
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["analysis_type"], "bivariate")

        # Invalid pagination parameters
        bad_limit = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda?limit=0"
        )
        self.assertEqual(bad_limit.status_code, 422)

        bad_offset = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda?offset=-1"
        )
        self.assertEqual(bad_offset.status_code, 422)

    def test_jev_absence_from_api_response(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # Verify no JEV internals leaked
        for key in ("decision", "reason_code", "selected_value", "audit_record", "claim_sha256"):
            self.assertNotIn(key, body)
            if "result" in body and isinstance(body["result"], dict):
                self.assertNotIn(key, body["result"])

    def test_migration_upgrade_and_downgrade(self):
        with self.engine.begin() as conn:
            # Drop table if created by Base.metadata.create_all
            conn.exec_driver_sql("DROP TABLE IF EXISTS eda_analyses;")
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            with patch.object(eda_migration, "op", op):
                eda_migration.upgrade()
                res = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='eda_analyses';"
                )
                self.assertEqual(res.scalar(), "eda_analyses")

                eda_migration.downgrade()
                res = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='eda_analyses';"
                )
                self.assertIsNone(res.scalar())

    def test_artifact_checksum_mismatch_fails_cleanly(self):
        file_id = self._setup_normalized_file()

        # Corrupt the parquet file in processed storage
        for (bucket, key) in list(self.storage.objects.keys()):
            if bucket == "insightos-processed":
                self.storage.objects[(bucket, key)]["content"] = b"corrupted bytes"

        res = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res.status_code, 409)
        self.assertIn(res.json()["detail"]["code"], ("normalized_output_failed", "normalized_output_stale", "artifact_integrity_failed", "source_integrity_failed"))

    def test_storage_read_failure_records_failed_status(self):
        file_id = self._setup_normalized_file()

        class FailingStream(BytesIO):
            def read(self, *args, **kwargs):
                raise OSError("simulated stream read failure")

        call_count = {"count": 0}

        def fail_get_object(bucket, key):
            if bucket == "insightos-processed":
                call_count["count"] += 1
                if call_count["count"] > 1:
                    return FailingStream(b"some content that will fail")
            return BytesIO(self.storage.objects[(bucket, key)]["content"])

        with patch.object(self.storage, "get_object", side_effect=fail_get_object):
            res = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
                json={"analysis_type": "univariate", "selected_columns": ["a"]},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "Failed")
            self.assertEqual(data["error"]["code"], "artifact_analysis_failed")

        session = self.session_factory()
        records = session.query(EDAAnalysis).filter(EDAAnalysis.file_id == file_id).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "Failed")
        self.assertEqual(records[0].error_code, "artifact_analysis_failed")
        session.close()

    def test_temporary_file_cleanup_on_success_and_failure(self):
        file_id = self._setup_normalized_file()
        created_paths = []
        original_named_temp = tempfile.NamedTemporaryFile

        def tracking_named_temp(*args, **kwargs):
            temp_file = original_named_temp(*args, **kwargs)
            created_paths.append(Path(temp_file.name))
            return temp_file

        with patch("tempfile.NamedTemporaryFile", side_effect=tracking_named_temp):
            res = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
                json={"analysis_type": "univariate", "selected_columns": ["a"]},
            )
            self.assertEqual(res.status_code, 200)

        self.assertTrue(len(created_paths) > 0)
        for path in created_paths:
            self.assertFalse(path.exists(), f"Temporary file {path} was not cleaned up on success!")

    def test_recompute_flag_in_json_body(self):
        file_id = self._setup_normalized_file()

        res1 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"]},
        )
        self.assertEqual(res1.status_code, 200)
        id1 = res1.json()["id"]

        # Body with recompute: true
        res2 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={"analysis_type": "univariate", "selected_columns": ["a"], "recompute": True},
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["id"], id1)


if __name__ == "__main__":
    unittest.main()
