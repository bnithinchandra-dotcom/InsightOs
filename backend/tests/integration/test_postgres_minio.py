import os
import unittest
import uuid

from fastapi.testclient import TestClient

from app import main
from app.models import Dataset, Project, User


@unittest.skipUnless(
    os.getenv("INSIGHTOS_RUN_INTEGRATION") == "1",
    "Requires a dedicated PostgreSQL database and MinIO instance.",
)
class PostgresMinioFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.storage = main.get_storage_client()
        cls.raw_bucket = os.getenv("MINIO_RAW_BUCKET", "insightos-raw")
        cls.processed_bucket = os.getenv(
            "MINIO_PROCESSED_BUCKET",
            "insightos-processed",
        )
        for bucket in (cls.raw_bucket, cls.processed_bucket):
            if not cls.storage.bucket_exists(bucket):
                cls.storage.make_bucket(bucket)

        session = main.SessionLocal()
        try:
            token = uuid.uuid4().hex
            user = User(email=f"integration-{token}@example.invalid")
            session.add(user)
            session.flush()
            project = Project(name="Integration test", user_id=user.id)
            session.add(project)
            session.flush()
            dataset = Dataset(
                project_id=project.id,
                name="Integration test",
                status="Created",
            )
            session.add(dataset)
            session.commit()
            cls.user_id = user.id
            cls.project_id = project.id
            cls.dataset_id = dataset.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.client.delete(f"/api/v1/datasets/{cls.dataset_id}")
        finally:
            session = main.SessionLocal()
            try:
                project = session.get(Project, cls.project_id)
                user = session.get(User, cls.user_id)
                if project is not None:
                    session.delete(project)
                if user is not None:
                    session.delete(user)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
                cls.client.close()

    def test_upload_normalization_and_quality_use_real_postgres_and_minio(self):
        projects = self.client.get("/api/v1/projects")
        self.assertEqual(projects.status_code, 200, projects.text)
        self.assertIn(self.project_id, [item["id"] for item in projects.json()])
        datasets = self.client.get(
            f"/api/v1/projects/{self.project_id}/datasets"
        )
        self.assertEqual(datasets.status_code, 200, datasets.text)
        self.assertEqual(
            [item["id"] for item in datasets.json()],
            [self.dataset_id],
        )

        source = b"code,value\n001,1.25\n002,2.50\n003,\n"
        uploaded = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("integration.csv", source, "text/csv")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        file_id = uploaded.json()["file"]["id"]
        storage_key = uploaded.json()["file"]["storage_key"]
        listed_files = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files"
        )
        self.assertEqual(listed_files.status_code, 200, listed_files.text)
        self.assertEqual(
            [item["id"] for item in listed_files.json()],
            [file_id],
        )

        stored = self.storage.get_object(self.raw_bucket, storage_key)
        try:
            self.assertEqual(stored.read(), source)
        finally:
            stored.close()
            stored.release_conn()

        normalized = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/normalize"
        )
        self.assertEqual(normalized.status_code, 200, normalized.text)
        normalized_body = normalized.json()
        self.assertEqual(normalized_body["status"], "Ready")
        self.assertEqual(
            normalized_body["normalization_result"]["validation_status"],
            "passed",
        )
        normalization_provenance = normalized_body["normalization_result"][
            "provenance"
        ]
        self.assertEqual(
            normalization_provenance["verification"]["status"],
            "verified",
        )
        self.assertTrue(
            main.verify_normalization_result(
                self.storage,
                self.processed_bucket,
                normalized_body["normalization_result"],
            )
        )

        quality = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/quality-analysis"
        )
        self.assertEqual(quality.status_code, 200, quality.text)
        quality_body = quality.json()
        self.assertEqual(quality_body["status"], "Completed")
        self.assertTrue(quality_body["report_is_current"])
        quality_provenance = quality_body["quality_report"]["provenance"]
        self.assertEqual(
            quality_provenance["verification"]["status"],
            "verified",
        )
        self.assertEqual(
            quality_provenance["parent_operation_ids"],
            [normalization_provenance["operation_id"]],
        )

        session = main.SessionLocal()
        try:
            file_record = session.get(main.DatasetFile, file_id)
            self.assertEqual(file_record.status, "Ready")
            self.assertEqual(file_record.normalization_status, "Ready")
            self.assertEqual(file_record.quality_analysis_status, "Completed")
        finally:
            session.close()

        eda_response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda",
            json={
                "analysis_type": "univariate",
                "table_index": 0,
                "selected_columns": ["code", "value"],
            },
        )
        self.assertEqual(eda_response.status_code, 200, eda_response.text)
        eda_body = eda_response.json()
        self.assertEqual(eda_body["status"], "Completed")
        self.assertEqual(eda_body["analysis_type"], "univariate")
        eda_id = eda_body["id"]
        eda_provenance = eda_body["provenance"]
        self.assertEqual(
            eda_provenance["parent_operation_ids"],
            [normalization_provenance["operation_id"]],
        )

        eda_get = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda/{eda_id}"
        )
        self.assertEqual(eda_get.status_code, 200, eda_get.text)
        self.assertEqual(eda_get.json()["id"], eda_id)

        eda_list = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files/{file_id}/eda"
        )
        self.assertEqual(eda_list.status_code, 200, eda_list.text)
        self.assertEqual(eda_list.json()["total"], 1)
        self.assertEqual(eda_list.json()["items"][0]["id"], eda_id)

        schema_response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={},
        )
        self.assertEqual(schema_response.status_code, 200, schema_response.text)
        schema_body = schema_response.json()
        self.assertEqual(schema_body["status"], "Completed")
        self.assertFalse(schema_body["stale"])
        self.assertEqual(schema_body["result"]["schema"]["table_count"], 1)
        self.assertEqual(
            schema_body["provenance"]["operation_type"],
            "schema_architect",
        )
        self.assertEqual(
            schema_body["provenance"]["verification"]["status"],
            "verified",
        )
        schema_id = schema_body["id"]
        recomputed = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={"recompute": True},
        )
        self.assertEqual(recomputed.status_code, 200, recomputed.text)
        self.assertEqual(recomputed.json()["id"], schema_id)
        self.assertEqual(recomputed.json()["status"], "Completed")

        for index in range(10):
            additional = self.client.post(
                f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
                json={"business_context": f"Disposable pagination case {index}"},
            )
            self.assertEqual(additional.status_code, 200, additional.text)
            self.assertEqual(additional.json()["status"], "Completed")

        schema_get = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/{schema_id}"
        )
        self.assertEqual(schema_get.status_code, 200, schema_get.text)
        self.assertEqual(schema_get.json()["id"], schema_id)
        self.assertFalse(schema_get.json()["stale"])
        schema_list = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses?limit=10&offset=0"
        )
        self.assertEqual(schema_list.status_code, 200, schema_list.text)
        self.assertEqual(schema_list.json()["total"], 11)
        self.assertEqual(len(schema_list.json()["items"]), 10)
        second_page = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses?limit=10&offset=10"
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        self.assertEqual(second_page.json()["total"], 11)
        self.assertEqual(len(second_page.json()["items"]), 1)

        session = main.SessionLocal()
        try:
            file_record = session.get(main.DatasetFile, file_id)
            file_record.profile_result = {"fixture_change": True}
            session.commit()
        finally:
            session.close()
        stale_result = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/{schema_id}"
        )
        self.assertEqual(stale_result.status_code, 200, stale_result.text)
        self.assertTrue(stale_result.json()["stale"])
        self.assertEqual(stale_result.json()["status"], "Stale")

        missing_analysis = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses/2147483647"
        )
        self.assertEqual(missing_analysis.status_code, 404, missing_analysis.text)
        invalid_selection = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/schema-analyses",
            json={
                "files": [
                    {"file_id": 2147483647, "table_indices": [0]},
                ],
            },
        )
        self.assertEqual(invalid_selection.status_code, 404, invalid_selection.text)

        session = main.SessionLocal()
        try:
            eda_record = session.get(main.EDAAnalysis, eda_id)
            self.assertIsNotNone(eda_record)
            self.assertEqual(eda_record.status, "Completed")
            schema_record = session.get(main.SchemaAnalysis, schema_id)
            self.assertIsNotNone(schema_record)
            self.assertEqual(schema_record.status, "Completed")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
