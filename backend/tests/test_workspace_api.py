import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base
from app.models import Dataset, DatasetFile, Project, User


class LocalWorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session_patch = patch.object(
            main,
            "SessionLocal",
            self.session_factory,
        )
        self.session_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.session_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_local_profile_is_one_time_and_exposes_no_login_claim(self):
        initial = self.client.get("/api/v1/workspace/profile")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json(), {"configured": False, "profile": None})

        created = self.client.post(
            "/api/v1/workspace/profile",
            json={
                "display_name": "Local Analyst",
                "email": "LOCAL-ANALYST@example.invalid",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["display_name"], "Local Analyst")
        self.assertEqual(created.json()["email"], "local-analyst@example.invalid")

        current = self.client.get("/api/v1/workspace/profile")
        self.assertTrue(current.json()["configured"])
        self.assertEqual(current.json()["profile"]["id"], created.json()["id"])

        duplicate = self.client.post(
            "/api/v1/workspace/profile",
            json={
                "display_name": "Another Analyst",
                "email": "another@example.invalid",
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(
            duplicate.json()["detail"]["code"],
            "workspace_profile_already_configured",
        )
        session = self.session_factory()
        self.assertEqual(session.query(User).count(), 1)
        session.close()

    def test_invalid_profile_input_does_not_create_a_user(self):
        response = self.client.post(
            "/api/v1/workspace/profile",
            json={"display_name": "  ", "email": "not-an-email"},
        )
        self.assertEqual(response.status_code, 422)
        session = self.session_factory()
        self.assertEqual(session.query(User).count(), 0)
        session.close()

    def test_project_creation_requires_exactly_one_local_owner(self):
        no_profile = self.client.post(
            "/api/v1/projects",
            json={"name": "My project"},
        )
        self.assertEqual(no_profile.status_code, 409)
        self.assertEqual(no_profile.json()["detail"]["code"], "workspace_profile_required")

        self.client.post(
            "/api/v1/workspace/profile",
            json={"display_name": "Local Analyst", "email": "analyst@example.invalid"},
        )
        created = self.client.post(
            "/api/v1/projects",
            json={"name": "My project"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["name"], "My project")

        session = self.session_factory()
        project = session.get(Project, created.json()["id"])
        self.assertEqual(project.user_id, 1)
        session.close()

        listed = self.client.get("/api/v1/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()], [created.json()["id"]])

    def test_project_creation_rejects_ambiguous_owner(self):
        session = self.session_factory()
        session.add_all(
            [
                User(display_name="First", email="first@example.invalid"),
                User(display_name="Second", email="second@example.invalid"),
            ]
        )
        session.commit()
        session.close()

        response = self.client.post("/api/v1/projects", json={"name": "My project"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "authentication_required")
        session = self.session_factory()
        self.assertEqual(session.query(Project).count(), 0)
        session.close()

    def test_project_and_dataset_access_are_scoped_to_the_workspace_owner(self):
        self.client.post(
            "/api/v1/workspace/profile",
            json={"display_name": "Local Analyst", "email": "analyst@example.invalid"},
        )
        created = self.client.post("/api/v1/projects", json={"name": "Owned project"})
        self.assertEqual(created.status_code, 201)

        session = self.session_factory()
        project = session.get(Project, created.json()["id"])
        dataset = Dataset(
            project_id=project.id,
            name="Owned dataset",
            status="Ready",
        )
        session.add(dataset)
        session.flush()
        dataset_file = DatasetFile(
            dataset_id=dataset.id,
            filename="source.csv",
            storage_key="raw/source.csv",
            file_size_bytes=1,
            status="Ready",
            profile_result={"row_count": 1},
        )
        session.add(dataset_file)
        session.flush()
        dataset_id = dataset.id
        file_id = dataset_file.id
        project.user_id = 999
        session.commit()
        session.close()

        project_response = self.client.get(f"/api/v1/projects/{created.json()['id']}")
        self.assertEqual(project_response.status_code, 404)

        dataset_response = self.client.post(
            f"/api/v1/projects/{created.json()['id']}/datasets",
            json={"name": "Owned dataset"},
        )
        self.assertEqual(dataset_response.status_code, 404)

        self.assertEqual(
            self.client.get(f"/api/v1/datasets/{dataset_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/datasets/{dataset_id}/files").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/datasets/{dataset_id}/files/{file_id}"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/datasets/{dataset_id}/files/{file_id}/profile"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/datasets/{dataset_id}/files",
                files={"file": ("new.csv", b"id\n1\n", "text/csv")},
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
