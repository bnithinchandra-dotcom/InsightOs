import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main, normalization
from app.database import Base
from app.migrations.versions import (
    c91d24fb0a53_add_schema_analyses as schema_migration,
)
from app.models import Dataset, DatasetFile, Project, SchemaAnalysis, User


class MissingObject(Exception):
    def __init__(self, code: str):
        self.code = code


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def bucket_exists(self, bucket):
        return any(object_bucket == bucket for object_bucket, _key in self.objects)

    def make_bucket(self, _bucket):
        return None

    def stat_object(self, bucket, key):
        try:
            stored = self.objects[(bucket, key)]
        except KeyError:
            raise MissingObject("NoSuchKey") from None
        metadata = stored["metadata"]
        return SimpleNamespace(
            size=len(stored["content"]),
            metadata=(
                {"x-amz-meta-sha256": metadata["sha256"]}
                if "sha256" in metadata
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
        for item in objects:
            self.remove_object(bucket, item.name)
        return []


class SchemaAnalysisApiTests(unittest.TestCase):
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
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )
        session = self.session_factory()
        user = User(email="schema-test@example.invalid")
        session.add(user)
        session.flush()
        project = Project(name="Schema Test Project", user_id=user.id)
        session.add(project)
        session.flush()
        dataset = Dataset(
            name="Schema Test Dataset",
            project_id=project.id,
            status="Created",
        )
        session.add(dataset)
        session.commit()
        self.dataset_id = dataset.id
        session.close()

        self.storage = FakeStorage()
        self.held_locks = set()

        def acquire_lock(session, connection, dataset_id, key):
            self.assertIs(session.get_bind(), connection)
            lock_name = f"{dataset_id}:{key}"
            if lock_name in self.held_locks:
                return None
            self.held_locks.add(lock_name)
            return lock_name

        def release_lock(session, connection, lock_id):
            self.assertIs(session.get_bind(), connection)
            self.held_locks.discard(lock_id)

        self.patches = [
            patch.object(main, "SessionLocal", self.session_factory),
            patch.object(main, "_try_acquire_upload_lock", side_effect=acquire_lock),
            patch.object(main, "_release_upload_lock", side_effect=release_lock),
            patch.object(main, "get_storage_client", return_value=self.storage),
            patch.object(main, "S3Error", MissingObject),
            patch.object(normalization, "S3Error", MissingObject),
            patch.dict(
                "os.environ",
                {
                    "MINIO_RAW_BUCKET": "insightos-raw",
                    "MINIO_PROCESSED_BUCKET": "insightos-processed",
                },
            ),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_normalized_file(
        self,
        filename="orders.csv",
        content=b"customer_id,amount\n1,5\n1,7\n2,3\n",
    ):
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={
                "file": (
                    filename,
                    content,
                    "text/csv",
                )
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        file_id = response.json()["file"]["id"]
        normalized = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/normalize"
        )
        self.assertEqual(normalized.status_code, 200, normalized.text)
        return file_id

    def test_run_idempotency_recompute_listing_and_retrieval(self):
        file_id = self._create_normalized_file()
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        analysis = response.json()
        self.assertEqual(analysis["status"], "Completed")
        self.assertFalse(analysis["stale"])
        self.assertEqual(
            analysis["result"]["schema"]["tables"][0]["identity"]["file_id"],
            file_id,
        )
        self.assertEqual(
            analysis["provenance"]["operation_type"],
            "schema_architect",
        )
        session = self.session_factory()
        stored_file = session.query(DatasetFile).one()
        object_key = stored_file.normalization_result["tables"][0]["object_key"]
        session.close()
        self.assertNotIn(object_key, str(analysis))
        self.assertNotIn("jev", str(analysis).lower())

        replay = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(replay.json()["id"], analysis["id"])
        recomputed = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={"recompute": True},
        )
        self.assertEqual(recomputed.status_code, 200, recomputed.text)
        self.assertEqual(recomputed.json()["id"], analysis["id"])

        listing = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses?limit=1&offset=0"
        )
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()["total"], 1)
        self.assertEqual(listing.json()["items"][0]["id"], analysis["id"])
        retrieved = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/{analysis['id']}"
        )
        self.assertEqual(retrieved.status_code, 200, retrieved.text)
        self.assertIn("schema", retrieved.json()["result"])
        self.assertIn("relationships", retrieved.json()["result"])
        self.assertIn("model_recommendations", retrieved.json()["result"])
        self.assertEqual(self.held_locks, set())

    def test_analysis_infers_relationship_across_multiple_files(self):
        orders_file_id = self._create_normalized_file()
        customers_file_id = self._create_normalized_file(
            "customers.csv",
            b"customer_id,name\n1,Ada\n2,Lin\n3,Sam\n",
        )
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "Completed")
        self.assertEqual(body["result"]["schema"]["table_count"], 2)
        relationship = next(
            candidate
            for candidate in body["result"]["relationships"]
            if candidate["status"] == "inferred"
        )
        self.assertEqual(relationship["source"]["table"]["file_id"], orders_file_id)
        self.assertEqual(
            relationship["target"]["table"]["file_id"],
            customers_file_id,
        )
        source_kinds = [
            item["kind"] for item in body["provenance"]["inputs"]
        ]
        self.assertEqual(source_kinds.count("source"), 2)
        self.assertEqual(source_kinds.count("normalized_parquet"), 2)

    def test_selected_tables_and_business_context_are_recorded(self):
        file_id = self._create_normalized_file()
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={
                "files": [
                    {
                        "file_id": file_id,
                        "table_indices": [0],
                    }
                ],
                "business_context": "Orders represent customer purchases.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        submitted = response.json()
        self.assertEqual(
            submitted["result"]["business_context"],
            "Orders represent customer purchases.",
        )
        self.assertEqual(submitted["configuration"]["files"][0]["file_id"], file_id)

    def test_stale_results_are_marked_and_exposed_in_retrieval(self):
        self._create_normalized_file()
        created = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        analysis_id = created.json()["id"]
        session = self.session_factory()
        dataset_file = session.query(DatasetFile).one()
        dataset_file.profile_result = {"changed": True}
        session.commit()
        session.close()

        current = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/{analysis_id}"
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertTrue(current.json()["stale"])
        self.assertEqual(current.json()["status"], "Stale")
        self.assertIsNotNone(current.json()["result"])

    def test_changed_normalized_object_identity_marks_analysis_stale(self):
        file_id = self._create_normalized_file()
        created = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(created.status_code, 200, created.text)
        session = self.session_factory()
        dataset_file = session.get(DatasetFile, file_id)
        normalization_result = deepcopy(dataset_file.normalization_result)
        normalization_result["tables"][0]["object_key"] += ".replacement"
        dataset_file.normalization_result = normalization_result
        session.commit()
        session.close()

        current = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/"
            f"{created.json()['id']}"
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertTrue(current.json()["stale"])

    def test_row_and_column_limits_fail_before_artifact_download(self):
        file_id = self._create_normalized_file()
        for limit_name, limit_value in (
            ("MAX_SCHEMA_ROWS", 2),
            ("MAX_SCHEMA_COLUMNS", 1),
        ):
            with self.subTest(limit=limit_name):
                with (
                    patch.object(main, limit_name, limit_value),
                    patch.object(main, "_schema_input_tables") as load_artifacts,
                ):
                    response = self.client.post(
                        f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                        json={
                            "files": [
                                {
                                    "file_id": file_id,
                                    "table_indices": [0],
                                }
                            ]
                        },
                    )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "resource_limit",
                )
                load_artifacts.assert_not_called()

    def test_unexpected_engine_failure_is_persisted_without_internal_details(self):
        self._create_normalized_file()
        with patch.object(
            main,
            "analyze_schema",
            side_effect=RuntimeError("private implementation detail"),
        ):
            response = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                json={},
            )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "schema_analysis_failed",
        )
        self.assertNotIn("private implementation detail", response.text)
        session = self.session_factory()
        record = session.query(SchemaAnalysis).one()
        self.assertEqual(record.status, "Failed")
        self.assertIsNone(record.result)
        self.assertIsNone(record.provenance)
        self.assertEqual(self.held_locks, set())
        session.close()

    def test_result_commit_failure_rolls_back_and_persists_failed_status(self):
        self._create_normalized_file()
        session_class = self.session_factory.class_
        original_commit = session_class.commit
        completion_commit_failed = False

        def fail_completed_commit(session):
            nonlocal completion_commit_failed
            completing = any(
                isinstance(record, SchemaAnalysis)
                and record.status == "Completed"
                for record in session.dirty
            )
            if completing and not completion_commit_failed:
                completion_commit_failed = True
                raise SQLAlchemyError("private persistence detail")
            return original_commit(session)

        with patch.object(
            session_class,
            "commit",
            new=fail_completed_commit,
        ):
            response = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                json={},
            )

        self.assertTrue(completion_commit_failed)
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "schema_analysis_persistence_failure",
        )
        self.assertNotIn("private persistence detail", response.text)
        session = self.session_factory()
        record = session.query(SchemaAnalysis).one()
        self.assertEqual(record.status, "Failed")
        self.assertIsNone(record.result)
        self.assertIsNone(record.provenance)
        session.close()

    def test_invalid_missing_and_conflicting_requests_return_stable_errors(self):
        unsupported = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={"unexpected": True},
        )
        self.assertEqual(unsupported.status_code, 422, unsupported.text)
        unsupported_nested = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={
                "files": [
                    {
                        "file_id": 1,
                        "table_indices": [0],
                        "unknown": "field",
                    }
                ]
            },
        )
        self.assertEqual(unsupported_nested.status_code, 422)

        missing = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={
                "files": [
                    {
                        "file_id": 999,
                        "table_indices": [0],
                    }
                ]
            },
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"]["code"], "dataset_file_not_found")

        file_id = self._create_normalized_file()
        invalid = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={
                "files": [
                    {
                        "file_id": file_id,
                        "table_indices": [99],
                    }
                ]
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["detail"]["code"], "invalid_selection")

        self.held_locks.add(f"{self.dataset_id}:eda:{file_id}")
        conflict = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"], "eda_in_progress")
        self.held_locks.clear()

    def test_dataset_deletion_conflicts_with_active_schema_analysis(self):
        self._create_normalized_file()
        analysis_started = Event()
        allow_analysis_to_finish = Event()
        original_analyze = main.analyze_schema

        def pause_analysis(tables):
            analysis_started.set()
            if not allow_analysis_to_finish.wait(timeout=10):
                raise TimeoutError("test did not release schema analysis")
            return original_analyze(tables)

        with (
            patch.object(main, "analyze_schema", side_effect=pause_analysis),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(
                self.client.post,
                f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                json={},
            )
            try:
                self.assertTrue(analysis_started.wait(timeout=5))
                deletion = self.client.delete(
                    f"/api/v1/datasets/{self.dataset_id}"
                )
                self.assertEqual(deletion.status_code, 409, deletion.text)
                self.assertEqual(
                    deletion.json()["detail"]["code"],
                    "normalization_in_progress",
                )
            finally:
                allow_analysis_to_finish.set()
            completed = future.result(timeout=10)

        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(
            self.client.delete(
                f"/api/v1/datasets/{self.dataset_id}"
            ).status_code,
            200,
        )
        session = self.session_factory()
        self.assertEqual(session.query(SchemaAnalysis).count(), 0)
        session.close()

    def test_concurrent_duplicate_requests_do_not_create_duplicate_records(self):
        self._create_normalized_file()
        analysis_started = Event()
        allow_analysis_to_finish = Event()
        original_analyze = main.analyze_schema

        def pause_analysis(tables):
            analysis_started.set()
            if not allow_analysis_to_finish.wait(timeout=10):
                raise TimeoutError("test did not release schema analysis")
            return original_analyze(tables)

        with (
            patch.object(main, "analyze_schema", side_effect=pause_analysis),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(
                self.client.post,
                f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                json={},
            )
            try:
                self.assertTrue(analysis_started.wait(timeout=5))
                duplicate = self.client.post(
                    f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                    json={},
                )
                self.assertEqual(duplicate.status_code, 409, duplicate.text)
                self.assertEqual(
                    duplicate.json()["detail"]["code"],
                    "schema_analysis_in_progress",
                )
            finally:
                allow_analysis_to_finish.set()
            completed = future.result(timeout=10)

        self.assertEqual(completed.status_code, 200, completed.text)
        session = self.session_factory()
        self.assertEqual(session.query(SchemaAnalysis).count(), 1)
        session.close()

    def test_missing_or_corrupt_artifact_fails_without_persisting_success(self):
        file_id = self._create_normalized_file()
        session = self.session_factory()
        dataset_file = session.get(DatasetFile, file_id)
        object_key = dataset_file.normalization_result["tables"][0]["object_key"]
        session.close()
        self.storage.objects[("insightos-processed", object_key)]["content"] = b"corrupt"

        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertIn(response.status_code, {409, 502})
        self.assertIn(
            response.json()["detail"]["code"],
            {"normalized_output_invalid", "artifact_storage_failure"},
        )
        session = self.session_factory()
        records = session.query(SchemaAnalysis).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "Failed")
        self.assertIsNone(records[0].result)
        session.close()

    def test_migration_upgrade_and_downgrade(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS schema_analyses")
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            with patch.object(schema_migration, "op", operations):
                schema_migration.upgrade()
                table = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='schema_analyses'"
                )
                self.assertEqual(table.scalar(), "schema_analyses")
                values = (
                    self.dataset_id,
                    "a" * 64,
                    "b" * 64,
                )
                connection.exec_driver_sql(
                    "INSERT INTO schema_analyses "
                    "(dataset_id, analysis_key, status, configuration, "
                    "configuration_sha256) "
                    "VALUES (?, ?, 'Completed', '{}', ?)",
                    values,
                )
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO schema_analyses "
                            "(dataset_id, analysis_key, status, configuration, "
                            "configuration_sha256) "
                            "VALUES (?, ?, 'Completed', '{}', ?)",
                            values,
                        )
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO schema_analyses "
                            "(dataset_id, analysis_key, status, configuration, "
                            "configuration_sha256) "
                            "VALUES (?, ?, 'Unknown', '{}', ?)",
                            (self.dataset_id, "c" * 64, "d" * 64),
                        )
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO schema_analyses "
                            "(dataset_id, analysis_key, status, configuration, "
                            "configuration_sha256) "
                            "VALUES (?, ?, 'Completed', '{}', ?)",
                            (999999, "e" * 64, "f" * 64),
                        )
                schema_migration.downgrade()
                table = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='schema_analyses'"
                )
                self.assertIsNone(table.scalar())


if __name__ == "__main__":
    unittest.main()
