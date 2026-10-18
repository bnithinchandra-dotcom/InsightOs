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
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pyarrow as pa
import pyarrow.parquet as pq

from app.database import Base
from app.models import Dataset, DatasetFile, EDAAnalysis, PreprocessingExecution, Project, User
from app import main, normalization, eda, preprocessing


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


class PreprocessingApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        session = self.session_factory()
        user = User(email="prep-test@example.invalid")
        session.add(user)
        session.flush()
        project = Project(name="Prep Test Project", user_id=user.id)
        session.add(project)
        session.flush()
        dataset = Dataset(
            name="Prep Test Dataset",
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
                "b": pa.array([10.0, 20.0, 30.0, 40.0], type=pa.float64()),
                "c": pa.array(["  alpha ", "beta  ", "  gamma  ", "delta"], type=pa.string()),
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

    def test_list_preprocessing_operations_endpoint(self):
        response = self.client.get("/api/v1/preprocessing/operations")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("operations", data)
        self.assertGreaterEqual(data["total"], 8)
        op_ids = [op["operation_id"] for op in data["operations"]]
        self.assertIn("da.string_normalization", op_ids)
        self.assertIn("da.missing_value_handling", op_ids)
        self.assertIn("da.numeric_outlier_handling", op_ids)
        self.assertIn("da.numeric_scaling", op_ids)
        self.assertIn("ba.business_rule_filter", op_ids)
        self.assertIn("ba.category_mapping", op_ids)
        self.assertIn("ba.derived_business_metric", op_ids)
        self.assertIn("ba.business_rule_validation", op_ids)

    def test_da_preprocessing_execution_persistence_and_artifact(self):
        file_id = self._setup_normalized_file()

        plan = {
            "table_index": 0,
            "steps": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True, "case": "upper"},
                }
            ],
            "approvals": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True, "case": "upper"},
                    "approved_approvals": ["approved_normalization_policy"],
                }
            ],
        }

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["dataset_id"], self.dataset_id)
        self.assertEqual(data["file_id"], file_id)
        self.assertEqual(data["status"], "Completed")
        self.assertIn("id", data)
        exec_id = data["id"]
        self.assertIsNotNone(data["artifact"])
        artifact = data["artifact"]
        self.assertIn("storage_key", artifact)
        self.assertIn("sha256", artifact)
        self.assertEqual(artifact["rows"], 4)
        self.assertEqual(artifact["columns"], 3)

        # Verify summary
        summary = data["result_summary"]
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["completed_steps"], ["step-trim"])
        self.assertEqual(len(summary["steps"]), 1)
        step_res = summary["steps"][0]
        self.assertEqual(step_res["step_id"], "step-trim")
        self.assertEqual(step_res["rows_after"], 4)

        # Verify provenance
        provenance = data["provenance"]
        self.assertEqual(provenance["verification_status"], "verified")
        self.assertGreaterEqual(len(provenance["inputs"]), 2)
        self.assertGreaterEqual(len(provenance["outputs"]), 1)

        # Verify stored Parquet artifact download
        dl_resp = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess/{exec_id}/artifact"
        )
        self.assertEqual(dl_resp.status_code, 200)
        derived_table = pq.read_table(BytesIO(dl_resp.content))
        self.assertEqual(derived_table.num_rows, 4)
        self.assertEqual(
            derived_table.column("c").to_pylist(),
            ["ALPHA", "BETA", "GAMMA", "DELTA"],
        )

    def test_ba_derived_metric_with_approval_execution(self):
        file_id = self._setup_normalized_file()

        plan = {
            "steps": [
                {
                    "step_id": "derive-metric",
                    "operation_id": "ba.derived_business_metric",
                    "configuration": {
                        "left_column": "b",
                        "operator": "multiply",
                        "right_column": "a",
                        "output_column": "metric_ab",
                    },
                }
            ],
            "approvals": [
                {
                    "step_id": "derive-metric",
                    "operation_id": "ba.derived_business_metric",
                    "configuration": {
                        "left_column": "b",
                        "operator": "multiply",
                        "right_column": "a",
                        "output_column": "metric_ab",
                    },
                    "approved_approvals": ["approved_business_metric_definition"],
                }
            ],
        }

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "Completed")
        self.assertEqual(data["artifact"]["columns"], 4)
        exec_id = data["id"]

        dl_resp = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess/{exec_id}/artifact"
        )
        self.assertEqual(dl_resp.status_code, 200)
        table = pq.read_table(BytesIO(dl_resp.content))
        self.assertIn("metric_ab", table.column_names)
        # a=[1, 2, 3, 4], b=[10.0, 20.0, 30.0, 40.0] -> a*b = [10.0, 40.0, 90.0, 160.0]
        self.assertEqual(table.column("metric_ab").to_pylist(), [10.0, 40.0, 90.0, 160.0])

    def test_missing_approval_fails_with_invalid_plan(self):
        file_id = self._setup_normalized_file()

        plan_no_approval = {
            "steps": [
                {
                    "step_id": "derive-metric",
                    "operation_id": "ba.derived_business_metric",
                    "configuration": {
                        "left_column": "b",
                        "operator": "multiply",
                        "right_column": "a",
                        "output_column": "metric_ab",
                    },
                }
            ],
            "approvals": [],
        }

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan_no_approval,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_plan")

    def test_unsupported_request_fields_rejected(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={
                "steps": [],
                "unsupported_key": 123,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_configuration")

    def test_step_unsupported_fields_rejected(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={
                "steps": [
                    {
                        "step_id": "s1",
                        "operation_id": "da.string_normalization",
                        "extra_key": True,
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_configuration")

    def test_invalid_table_index_rejected(self):
        file_id = self._setup_normalized_file()

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={"table_index": 999, "steps": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "unknown_table")

    def test_idempotency_and_cached_replay(self):
        file_id = self._setup_normalized_file()

        plan = {
            "steps": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                }
            ],
            "approvals": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                    "approved_approvals": ["approved_normalization_policy"],
                }
            ],
        }

        resp1 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(resp1.status_code, 200)
        id1 = resp1.json()["id"]

        # Second call without recompute returns the cached execution
        resp2 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["id"], id1)

        # Call with recompute=True recomputes
        resp3 = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={**plan, "recompute": True},
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.json()["id"], id1)
        self.assertEqual(resp3.json()["status"], "Completed")

    def test_step_runtime_failure_records_failed_execution(self):
        file_id = self._setup_normalized_file()

        # Divide by zero with error policy
        plan_failing = {
            "steps": [
                {
                    "step_id": "divide-step",
                    "operation_id": "ba.derived_business_metric",
                    "configuration": {
                        "left_column": "a",
                        "operator": "divide",
                        "right_column": "b",
                        "output_column": "div_ab",
                        "divide_by_zero": "error",
                    },
                }
            ],
            "approvals": [
                {
                    "step_id": "divide-step",
                    "operation_id": "ba.derived_business_metric",
                    "configuration": {
                        "left_column": "a",
                        "operator": "divide",
                        "right_column": "b",
                        "output_column": "div_ab",
                        "divide_by_zero": "error",
                    },
                    "approved_approvals": ["approved_business_metric_definition"],
                }
            ],
        }

        # Modify column b to contain a 0.0 to trigger divide-by-zero
        table_with_zero = pa.table(
            {
                "a": pa.array([1, 2], type=pa.int64()),
                "b": pa.array([10.0, 0.0], type=pa.float64()),
                "c": pa.array(["x", "y"], type=pa.string()),
            }
        )
        buf = BytesIO()
        pq.write_table(table_with_zero, buf)
        up = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("zero.parquet", buf.getvalue(), "application/vnd.apache.parquet")},
        )
        f_zero_id = up.json()["file"]["id"]
        self.client.post(f"/api/v1/datasets/{self.dataset_id}/files/{f_zero_id}/normalize")

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{f_zero_id}/preprocess",
            json=plan_failing,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "Failed")
        self.assertIsNotNone(data["error"])
        self.assertEqual(data["error"]["code"], "step_execution_failed")
        self.assertIsNone(data["artifact"])

    def test_list_and_get_preprocessing_executions(self):
        file_id = self._setup_normalized_file()

        plan = {
            "steps": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                }
            ],
            "approvals": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                    "approved_approvals": ["approved_normalization_policy"],
                }
            ],
        }

        post_resp = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(post_resp.status_code, 200)
        exec_id = post_resp.json()["id"]

        # List
        list_resp = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess"
        )
        self.assertEqual(list_resp.status_code, 200)
        data = list_resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], exec_id)

        # Get
        get_resp = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess/{exec_id}"
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["id"], exec_id)
        self.assertEqual(get_resp.json()["status"], "Completed")

        # 404 for nonexistent
        not_found = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess/99999"
        )
        self.assertEqual(not_found.status_code, 404)

    def test_concurrency_conflicts_with_active_locks(self):
        file_id = self._setup_normalized_file()

        # 1. Active preprocessing lock conflicts with concurrent preprocessing
        self.held_locks.add(f"{self.dataset_id}:preprocessing:{file_id}")
        conflict_resp = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={"steps": []},
        )
        self.assertEqual(conflict_resp.status_code, 409)
        self.assertEqual(conflict_resp.json()["detail"]["code"], "preprocessing_in_progress")
        self.held_locks.remove(f"{self.dataset_id}:preprocessing:{file_id}")

        # 2. Active normalization lock conflicts with preprocessing
        self.held_locks.add(f"{self.dataset_id}:normalization:{file_id}")
        conflict_norm = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json={"steps": []},
        )
        self.assertEqual(conflict_norm.status_code, 409)
        self.assertEqual(conflict_norm.json()["detail"]["code"], "normalization_in_progress")
        self.held_locks.remove(f"{self.dataset_id}:normalization:{file_id}")

        # 3. Active preprocessing lock conflicts with normalization
        self.held_locks.add(f"{self.dataset_id}:preprocessing:{file_id}")
        conflict_norm_run = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/normalize"
        )
        self.assertEqual(conflict_norm_run.status_code, 409)
        self.assertEqual(conflict_norm_run.json()["detail"]["code"], "preprocessing_in_progress")
        self.held_locks.remove(f"{self.dataset_id}:preprocessing:{file_id}")

        # 4. Active preprocessing lock conflicts with dataset deletion
        self.held_locks.add(f"{self.dataset_id}:preprocessing:{file_id}")
        del_conflict = self.client.delete(f"/api/v1/datasets/{self.dataset_id}")
        self.assertEqual(del_conflict.status_code, 409)
        self.assertEqual(del_conflict.json()["detail"]["code"], "preprocessing_in_progress")
        self.held_locks.remove(f"{self.dataset_id}:preprocessing:{file_id}")

    def test_dataset_deletion_removes_preprocessing_artifacts_and_records(self):
        file_id = self._setup_normalized_file()

        plan = {
            "steps": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                }
            ],
            "approvals": [
                {
                    "step_id": "step-trim",
                    "operation_id": "da.string_normalization",
                    "configuration": {"columns": ["c"], "trim": True},
                    "approved_approvals": ["approved_normalization_policy"],
                }
            ],
        }

        resp = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/preprocess",
            json=plan,
        )
        self.assertEqual(resp.status_code, 200)
        exec_id = resp.json()["id"]
        artifact_key = resp.json()["artifact"]["storage_key"]
        self.assertIn(("insightos-processed", artifact_key), self.storage.objects)

        # Delete dataset
        del_resp = self.client.delete(f"/api/v1/datasets/{self.dataset_id}")
        self.assertEqual(del_resp.status_code, 200)

        # Verify storage artifact is deleted
        self.assertNotIn(("insightos-processed", artifact_key), self.storage.objects)

        # Verify DB record is cascaded
        session = self.session_factory()
        record = session.get(PreprocessingExecution, exec_id)
        self.assertIsNone(record)
        session.close()


if __name__ == "__main__":
    unittest.main()
