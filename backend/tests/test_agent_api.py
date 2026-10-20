import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.agent import api as chat_api
from app.agent.contracts import AgentOutcome, UsageMetadata
from app.database import Base
from app.migrations.versions import (
    da7304d9fe22_add_conversations as conversation_migration,
)
from app.models import Conversation, ConversationMessage, Dataset, Project, User
from alembic.migration import MigrationContext
from alembic.operations import Operations


class ConversationApiTests(unittest.TestCase):
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
        with self.session_factory() as session:
            user = User(email="assistant-test@example.invalid")
            session.add(user)
            session.flush()
            project = Project(name="Assistant fixture", user_id=user.id)
            other_project = Project(name="Other assistant fixture", user_id=user.id)
            session.add(project)
            session.add(other_project)
            session.flush()
            dataset = Dataset(
                name="Assistant dataset",
                project_id=project.id,
                status="Ready",
            )
            session.add(dataset)
            session.commit()
            self.project_id = project.id
            self.other_project_id = other_project.id
            self.dataset_id = dataset.id

        self.session_patch = patch.object(chat_api, "SessionLocal", self.session_factory)
        self.session_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.session_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_conversation(self, payload=None):
        response = self.client.post(
            "/api/v1/chat/conversations",
            json=payload or {},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_list_and_paginate_conversation_messages(self):
        conversation = self._create_conversation({
            "title": "Existing data",
            "project_id": self.project_id,
            "dataset_id": self.dataset_id,
        })
        with self.session_factory() as session:
            for sequence in range(1, 4):
                session.add(
                    ConversationMessage(
                        conversation_id=conversation["id"],
                        sequence=sequence,
                        role="user" if sequence % 2 else "assistant",
                        content=f"message {sequence}",
                    )
                )
            session.commit()

        listed = self.client.get("/api/v1/chat/conversations?limit=1&offset=0")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        page = self.client.get(
            f"/api/v1/chat/conversations/{conversation['id']}?limit=2&offset=1"
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total_messages"], 3)
        self.assertEqual([item["sequence"] for item in page.json()["messages"]], [2, 3])

    def test_invalid_context_ids_and_mismatched_dataset_return_client_errors(self):
        missing = self.client.post(
            "/api/v1/chat/conversations",
            json={"project_id": 9999},
        )
        self.assertEqual(missing.status_code, 404)

        invalid = self.client.post(
            "/api/v1/chat/conversations",
            json={"project_id": str(self.project_id)},
        )
        self.assertEqual(invalid.status_code, 422)

        mismatch = self.client.post(
            "/api/v1/chat/conversations",
            json={"project_id": self.other_project_id, "dataset_id": self.dataset_id},
        )
        self.assertEqual(mismatch.status_code, 422)

        invalid_path = self.client.get("/api/v1/chat/conversations/0")
        self.assertEqual(invalid_path.status_code, 422)

    def test_send_persists_user_and_assistant_outcome(self):
        conversation = self._create_conversation()
        outcome = AgentOutcome(
            status="completed",
            assistant_text="The stored profile is ready.",
            activities=(),
            structured_results=(),
            warnings=(),
            usage=UsageMetadata(input_tokens=12, output_tokens=8),
        )
        with patch.object(chat_api, "execute_agent_request", return_value=outcome):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Summarize my profile"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_status"], "completed")
        detail = self.client.get(f"/api/v1/chat/conversations/{conversation['id']}")
        self.assertEqual(
            [message["role"] for message in detail.json()["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(detail.json()["messages"][1]["content"], "The stored profile is ready.")
        self.assertEqual(detail.json()["status"], "Active")

    def test_unexpected_agent_failure_is_saved_as_failed_not_success(self):
        conversation = self._create_conversation()
        with patch.object(
            chat_api,
            "execute_agent_request",
            side_effect=RuntimeError("must not leak"),
        ):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Analyze existing data"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_status"], "failed")
        self.assertNotIn("must not leak", response.text)
        detail = self.client.get(f"/api/v1/chat/conversations/{conversation['id']}")
        self.assertEqual(detail.json()["status"], "Active")
        self.assertEqual(
            detail.json()["messages"][1]["metadata"]["error_code"],
            "agent_execution_failed",
        )

    def test_active_processing_conversation_rejects_duplicate_send(self):
        conversation = self._create_conversation()
        with self.session_factory() as session:
            record = session.get(Conversation, conversation["id"])
            record.status = "Processing"
            record.processing_started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
            record.processing_token = "active-processing-token"
            session.commit()
        with patch.object(chat_api, "execute_agent_request") as execute:
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Do not start twice"},
            )
        self.assertEqual(response.status_code, 409)
        execute.assert_not_called()
        with self.session_factory() as session:
            self.assertEqual(session.query(ConversationMessage).count(), 0)

    def test_superseded_execution_cannot_persist_a_stale_response(self):
        conversation = self._create_conversation()
        outcome = AgentOutcome(
            status="completed",
            assistant_text="A stale answer.",
            activities=(),
            structured_results=(),
            warnings=(),
            usage=UsageMetadata(),
        )

        def supersede_request(**_kwargs):
            with self.session_factory() as session:
                record = session.get(Conversation, conversation["id"])
                record.processing_started_at = (
                    datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(seconds=3_000)
                )
                record.processing_token = "newer-processing-token"
                session.commit()
            return outcome

        with patch.object(
            chat_api,
            "PROCESSING_LEASE_SECONDS",
            1,
        ), patch.object(
            chat_api,
            "execute_agent_request",
            side_effect=supersede_request,
        ):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Original request"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("A stale answer.", response.text)
        with self.session_factory() as session:
            record = session.get(Conversation, conversation["id"])
            self.assertEqual(record.status, "Processing")
            self.assertIsNotNone(record.processing_token)
            self.assertEqual(session.query(ConversationMessage).count(), 1)

    def test_conversation_migration_upgrade_and_downgrade(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE IF EXISTS conversation_messages")
            connection.exec_driver_sql("DROP TABLE IF EXISTS conversations")
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            with patch.object(conversation_migration, "op", operations):
                conversation_migration.upgrade()
                names = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("conversations", names)
                self.assertIn("conversation_messages", names)
                timestamp = "2026-10-19 10:00:00"
                connection.exec_driver_sql(
                    "INSERT INTO conversations "
                    "(title, status, project_id, dataset_id, created_at, updated_at) "
                    "VALUES ('Fixture', 'Active', ?, ?, ?, ?)",
                    (self.project_id, self.dataset_id, timestamp, timestamp),
                )
                conversation_id = connection.exec_driver_sql(
                    "SELECT id FROM conversations"
                ).scalar_one()
                connection.exec_driver_sql(
                    "INSERT INTO conversation_messages "
                    "(conversation_id, sequence, role, content, created_at) "
                    "VALUES (?, 1, 'user', 'Hello', ?)",
                    (conversation_id, timestamp),
                )
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO conversation_messages "
                            "(conversation_id, sequence, role, content, created_at) "
                            "VALUES (?, 1, 'assistant', 'Duplicate', ?)",
                            (conversation_id, timestamp),
                        )
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO conversation_messages "
                            "(conversation_id, sequence, role, content, created_at) "
                            "VALUES (?, 0, 'user', 'Invalid sequence', ?)",
                            (conversation_id, timestamp),
                        )
                connection.exec_driver_sql(
                    "DELETE FROM datasets WHERE id = ?",
                    (self.dataset_id,),
                )
                self.assertIsNone(
                    connection.exec_driver_sql(
                        "SELECT dataset_id FROM conversations WHERE id = ?",
                        (conversation_id,),
                    ).scalar_one()
                )
                connection.exec_driver_sql(
                    "DELETE FROM conversations WHERE id = ?",
                    (conversation_id,),
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "SELECT count(*) FROM conversation_messages"
                    ).scalar_one(),
                    0,
                )
                conversation_migration.downgrade()
                names = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("conversations", names)
                self.assertNotIn("conversation_messages", names)


if __name__ == "__main__":
    unittest.main()
