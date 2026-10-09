import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Dataset, DatasetFile, Project, User
from app import main


class MissingObject(Exception):
    def __init__(self, code: str):
        self.code = code


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def bucket_exists(self, _bucket):
        return False

    def make_bucket(self, _bucket):
        return None

    def stat_object(self, _bucket, _key):
        raise MissingObject("NoSuchKey")

    def put_object(self, bucket, key, stream, _size, content_type=None):
        self.objects[(bucket, key)] = {
            "content": stream.read(),
            "content_type": content_type,
        }

    def remove_object(self, bucket, key):
        self.objects.pop((bucket, key), None)


class UploadFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        session = self.session_factory()
        user = User(email="upload-test@example.invalid")
        session.add(user)
        session.flush()
        project = Project(name="Test project", user_id=user.id)
        session.add(project)
        session.flush()
        self.project_id = project.id
        dataset = Dataset(project_id=project.id, name="Test dataset", status="Created")
        session.add(dataset)
        session.commit()
        self.dataset_id = dataset.id
        session.close()

        self.storage = FakeStorage()
        self.patches = [
            patch.object(main, "SessionLocal", self.session_factory),
            patch.object(main, "get_storage_client", return_value=self.storage),
            patch.object(main, "S3Error", MissingObject),
        ]
        for active_patch in self.patches:
            active_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_upload_parses_persists_and_preserves_original_bytes(self):
        content = b'"id",name\r\n1,Ada\r\n2,Lin\r\n'
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("people.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["dataset_status"], "Uploaded")
        self.assertEqual(payload["file"]["detected_format"], "csv")
        self.assertEqual(payload["file"]["parsing_result"]["row_count"], 2)
        self.assertEqual(len(self.storage.objects), 1)
        stored = next(iter(self.storage.objects.values()))
        self.assertEqual(stored["content"], content)

        session = self.session_factory()
        dataset = session.get(Dataset, self.dataset_id)
        dataset_file = session.query(DatasetFile).one()
        self.assertEqual(dataset.status, "Uploaded")
        self.assertEqual(dataset_file.detected_format, "csv")
        self.assertEqual(dataset_file.parsing_result["columns"][0]["name"], "id")
        session.close()

        files_response = self.client.get(
            f"/api/v1/datasets/{self.dataset_id}/files"
        )
        self.assertEqual(files_response.status_code, 200)
        self.assertEqual(
            files_response.json()[0]["parsing_result"]["row_count"],
            2,
        )

    def test_parse_failure_sets_failed_without_storing_or_recording_file(self):
        response = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("wrong.csv", b'{"records":[{"x":1}]}', "text/csv")},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["code"], "content_mismatch")
        self.assertEqual(self.storage.objects, {})
        session = self.session_factory()
        self.assertEqual(session.get(Dataset, self.dataset_id).status, "Failed")
        self.assertEqual(session.query(DatasetFile).count(), 0)
        session.close()

    def test_stage_24_type_validation_and_existing_health_endpoints(self):
        mismatch = self.client.post(
            f"/api/v1/datasets/{self.dataset_id}/files",
            files={"file": ("book.xlsx", b"content", "text/csv")},
        )
        self.assertEqual(mismatch.status_code, 415)

        self.assertEqual(self.client.get("/health").json()["status"], "healthy")
        with patch.object(main, "check_database", return_value=True):
            self.assertEqual(
                self.client.get("/health/database").json()["status"],
                "healthy",
            )
        with patch.object(main, "check_redis", return_value=True):
            self.assertEqual(
                self.client.get("/health/redis").json()["status"],
                "healthy",
            )
        with patch.object(main, "check_storage", return_value=(True, [])):
            self.assertEqual(
                self.client.get("/health/storage").json()["status"],
                "healthy",
            )
        with (
            patch.object(main, "check_database", return_value=True),
            patch.object(main, "check_redis", return_value=True),
            patch.object(main, "check_storage", return_value=(True, [])),
        ):
            self.assertEqual(
                self.client.get("/api/v1/system/info").json()["services"]["backend"],
                "healthy",
            )

    def test_dataset_creation_retrieval_listing_and_safe_deletion(self):
        created = self.client.post(
            f"/api/v1/projects/{self.project_id}/datasets",
            json={"name": "Created through API"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        dataset_id = created.json()["id"]
        self.assertEqual(
            self.client.get(f"/api/v1/datasets/{dataset_id}").json()["status"],
            "Created",
        )
        listed = self.client.get(
            f"/api/v1/projects/{self.project_id}/datasets"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertIn(dataset_id, [item["id"] for item in listed.json()])

        deleted = self.client.delete(f"/api/v1/datasets/{dataset_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted"], True)
        self.assertEqual(
            self.client.get(f"/api/v1/datasets/{dataset_id}").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
