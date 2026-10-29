import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.agent import api as chat_api
from app.agent.config import AgentSettings
from app.agent.contracts import (
    AgentOutcome,
    ModelIdentity,
    ModelResponse,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    ProviderMessage,
    ToolCall,
    ToolResult,
    UsageMetadata,
)
from app.agent.specialist_agents import DataIntelligenceResult
from app.agent import runtime as agent_runtime
from app.database import Base
from app.migrations.versions import (
    da7304d9fe22_add_conversations as conversation_migration,
)
from app.models import (
    Conversation,
    ConversationMessage,
    Dataset,
    DatasetFile,
    Project,
    User,
    WorkflowExecution,
    WorkflowTask,
)
from tests.test_agent_orchestrator import FakeRegistry, build_handoff
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
            self.user_id = user.id
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

    def test_conversation_access_rechecks_project_ownership(self):
        conversation = self._create_conversation()
        with self.session_factory() as session:
            owner = session.query(User).one()
            foreign_user = User(email="foreign-owner@example.invalid")
            session.add(foreign_user)
            session.flush()
            foreign_project = Project(
                name="Foreign project",
                user_id=foreign_user.id,
            )
            session.add(foreign_project)
            session.flush()
            stored_conversation = session.get(Conversation, conversation["id"])
            stored_conversation.project_id = foreign_project.id
            session.commit()
            owner_id = owner.id

        # Exercise the resource-scope check with a fixed selected owner. In the
        # real local-profile flow, multiple profiles are rejected before access.
        with patch.object(
            chat_api,
            "_require_workspace_owner",
            side_effect=lambda session: session.get(User, owner_id),
        ):
            retrieved = self.client.get(
                f"/api/v1/chat/conversations/{conversation['id']}"
            )
            sent = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Read this conversation"},
            )

        self.assertEqual(retrieved.status_code, 404)
        self.assertEqual(sent.status_code, 404)

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

    def test_api_runs_model_tool_loop_with_persisted_history(self):
        conversation = self._create_conversation()

        class SequenceProvider:
            def __init__(self):
                self.responses = iter((
                    ModelResponse(
                        ProviderMessage(
                            role="assistant",
                            content="Hello from the configured model.",
                        ),
                        finish_reason="stop",
                    ),
                    ModelResponse(
                        ProviderMessage(
                            role="assistant",
                            content="",
                            tool_calls=(
                                ToolCall("call-1", "list_projects", {}),
                            ),
                        ),
                        finish_reason="tool_calls",
                    ),
                    ModelResponse(
                        ProviderMessage(
                            role="assistant",
                            content=(
                                "The available projects are Assistant fixture "
                                "and Other assistant fixture."
                            ),
                        ),
                        finish_reason="stop",
                    ),
                ))
                self.requests = []

            def complete(self, request):
                self.requests.append(request)
                return next(self.responses)

        provider = SequenceProvider()
        settings = AgentSettings(
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="qwen3:4b-instruct",
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=3,
            max_tool_calls=2,
            max_history_messages=4,
        )
        with patch.object(main, "SessionLocal", self.session_factory), patch.object(
            agent_runtime,
            "get_agent_settings",
            return_value=settings,
        ), patch.object(
            agent_runtime,
            "create_provider",
            return_value=provider,
        ):
            greeting = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Hello"},
            )
            tool_turn = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "List my projects"},
            )

        self.assertEqual(greeting.status_code, 200, greeting.text)
        self.assertEqual(
            greeting.json()["assistant_message"]["content"],
            "Hello from the configured model.",
        )
        self.assertEqual(tool_turn.status_code, 200, tool_turn.text)
        self.assertEqual(tool_turn.json()["execution_status"], "completed")
        self.assertEqual(
            tool_turn.json()["assistant_message"]["content"],
            "The available projects are Assistant fixture and Other assistant fixture.",
        )
        self.assertEqual(
            tool_turn.json()["tool_activities"][0]["tool_name"],
            "list_projects",
        )
        self.assertEqual(
            tool_turn.json()["tool_activities"][0]["status"],
            "succeeded",
        )
        self.assertIsInstance(
            tool_turn.json()["structured_results"][0]["data"][0]["created_at"],
            str,
        )
        workflow_response = self.client.get(
            f"/api/v1/chat/workflows/{tool_turn.json()['workflow_id']}"
        )
        self.assertEqual(workflow_response.status_code, 200)
        self.assertEqual(
            [task["task_type"] for task in workflow_response.json()["tasks"]],
            ["list_projects"],
        )
        self.assertEqual(
            workflow_response.json()["tasks"][0]["status"],
            "completed",
        )
        self.assertEqual(
            [(message.role, message.content) for message in provider.requests[1].messages[-3:]],
            [
                ("user", "Hello"),
                ("assistant", "Hello from the configured model."),
                ("user", "List my projects"),
            ],
        )
        self.assertEqual(provider.requests[2].messages[-1].role, "tool")
        detail = self.client.get(
            f"/api/v1/chat/conversations/{conversation['id']}"
        ).json()
        self.assertEqual(
            [message["role"] for message in detail["messages"]],
            ["user", "assistant", "user", "assistant"],
        )

    def test_dataset_inspection_orchestrator_persists_a_verified_chat_answer(self):
        conversation = self._create_conversation({"dataset_id": self.dataset_id})
        registry = FakeRegistry(build_handoff(self.dataset_id))
        with patch("app.agent.runtime.ToolRegistry", return_value=registry):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "How many rows and columns are in this dataset?"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["execution_status"], "completed")
        self.assertRegex(body["workflow_id"], r"^[0-9a-f]{32}$")
        self.assertIn("has 3 rows", body["assistant_message"]["content"])
        self.assertIn("has 2 columns", body["assistant_message"]["content"])
        trace = next(
            result["data"]
            for result in body["structured_results"]
            if result["tool"] == "orchestration_trace"
        )
        self.assertEqual(trace["dataset_id"], self.dataset_id)
        self.assertEqual(trace["outcome"], "completed")
        self.assertEqual(
            [call[0] for call in registry.calls],
            ["list_files", "get_inspection_handoff"],
        )
        workflow_response = self.client.get(
            f"/api/v1/chat/workflows/{body['workflow_id']}"
        )
        self.assertEqual(workflow_response.status_code, 200, workflow_response.text)
        workflow = workflow_response.json()
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(workflow["conversation_id"], conversation["id"])
        self.assertEqual(workflow["source_scope"]["dataset_id"], self.dataset_id)
        self.assertTrue(workflow["source_scope"]["source_fingerprint"])
        self.assertEqual(
            [task["task_type"] for task in workflow["tasks"]],
            ["list_files", "get_inspection_handoff"],
        )
        self.assertTrue(
            all(task["status"] == "completed" for task in workflow["tasks"])
        )
        self.assertTrue(
            all(task["result_summary"] for task in workflow["tasks"])
        )
        self.assertTrue(
            all(task["input_references"] for task in workflow["tasks"])
        )
        self.assertEqual(workflow["tasks"][0]["depends_on"], [])
        self.assertEqual(
            workflow["tasks"][1]["depends_on"],
            [workflow["tasks"][0]["task_id"]],
        )
        with self.session_factory() as session:
            persisted = session.get(WorkflowExecution, body["workflow_id"])
            self.assertEqual(persisted.objective, "How many rows and columns are in this dataset?")
            self.assertEqual(session.query(WorkflowTask).filter_by(
                workflow_id=body["workflow_id"]
            ).count(), 2)
        missing_workflow = self.client.get(
            "/api/v1/chat/workflows/" + ("0" * 32)
        )
        self.assertEqual(missing_workflow.status_code, 404)

    def test_scheduler_reuses_completed_read_for_identical_source_identity(self):
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="a" * 32,
                owner_user_id=self.user_id,
                idempotency_key="b" * 32,
                objective="Inspect the selected dataset",
                status="running",
                current_step=0,
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "a" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        executions = []

        def execute():
            executions.append("ran")
            result = ToolResult(
                status="succeeded",
                summary="Dataset files were listed.",
                data=[{"id": 1, "status": "Ready"}],
                references=({"type": "dataset_file", "dataset_id": self.dataset_id},),
            )
            return "{}", result, "Listing files"

        first = schedule(
            "list_files",
            {"dataset_id": self.dataset_id},
            execute,
            idempotent=True,
            max_retries=1,
        )
        second = schedule(
            "list_files",
            {"dataset_id": self.dataset_id},
            execute,
            idempotent=True,
            max_retries=1,
        )
        with self.session_factory() as session:
            dataset = session.get(Dataset, self.dataset_id)
            dataset.updated_at += timedelta(seconds=1)
            session.commit()
        third = schedule(
            "list_files",
            {"dataset_id": self.dataset_id},
            execute,
            idempotent=True,
            max_retries=1,
        )

        self.assertEqual(executions, ["ran", "ran"])
        self.assertEqual(first[1].data, second[1].data)
        self.assertEqual(first[3], second[3])
        self.assertNotEqual(second[3], third[3])
        with self.session_factory() as session:
            tasks = session.query(WorkflowTask).filter_by(
                workflow_id="a" * 32
            ).all()
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all(task.status == "completed" for task in tasks))
            self.assertEqual(
                tasks[0].output_references[0]["type"],
                "dataset_file",
            )

    def test_specialist_tasks_persist_role_dependencies_and_versioned_handoffs(self):
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="9" * 32,
                owner_user_id=self.user_id,
                dataset_id=self.dataset_id,
                idempotency_key="8" * 32,
                objective="Run selected-file analysis",
                status="running",
                current_step=0,
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "9" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        executions = []

        def stage_execution(role, task_type):
            def execute():
                executions.append(role)
                with self.session_factory() as session:
                    persisted = (
                        session.query(WorkflowTask)
                        .filter_by(
                            workflow_id="9" * 32,
                            task_type=task_type,
                            agent=role,
                            status="running",
                        )
                        .order_by(WorkflowTask.sequence.desc())
                        .first()
                    )
                    self.assertIsNotNone(persisted)
                    self.assertIsNotNone(persisted.started_at)
                    for edge in persisted.dependencies:
                        dependency = session.get(
                            WorkflowTask,
                            edge.depends_on_task_id,
                        )
                        self.assertEqual(dependency.status, "completed")
                return "{}", ToolResult(
                    status="succeeded",
                    summary=f"{role} completed.",
                    data={
                        "handoff": {
                            "stage": role,
                            "source_fingerprint": "source-v1",
                            "dataset_id": self.dataset_id,
                            "file_id": 1,
                            "verified": True,
                        }
                    },
                    references=(
                        {
                            "type": "specialist_handoff",
                            "source_fingerprint": "source-v1",
                            "dataset_id": self.dataset_id,
                            "file_id": 1,
                        },
                    ),
                ), role

            return execute

        data_arguments = {
            "dataset_id": self.dataset_id,
            "file_id": 1,
            "requested_operation": "run_eda_analysis",
            "stage_input_fingerprint": "a" * 64,
        }
        data_task = schedule(
            "specialist_data_intelligence",
            data_arguments,
            stage_execution("data_intelligence", "specialist_data_intelligence"),
            idempotent=True,
            max_retries=1,
            agent="data_intelligence",
        )
        reused_data_task = schedule(
            "specialist_data_intelligence",
            data_arguments,
            stage_execution("data_intelligence", "specialist_data_intelligence"),
            idempotent=True,
            max_retries=1,
            agent="data_intelligence",
        )
        changed_data_task = schedule(
            "specialist_data_intelligence",
            {**data_arguments, "stage_input_fingerprint": "b" * 64},
            stage_execution("data_intelligence", "specialist_data_intelligence"),
            idempotent=True,
            max_retries=1,
            agent="data_intelligence",
        )
        analytics_task = schedule(
            "specialist_analytics_modelling",
            {
                **data_arguments,
                "stage_input_fingerprint": "c" * 64,
            },
            stage_execution("analytics_modelling", "specialist_analytics_modelling"),
            idempotent=True,
            max_retries=1,
            depends_on=[data_task[3]],
            agent="analytics_modelling",
        )

        self.assertEqual(len(executions), 3)
        self.assertEqual(data_task[3], reused_data_task[3])
        self.assertNotEqual(data_task[3], changed_data_task[3])
        with self.session_factory() as session:
            tasks = {
                task.task_key: task
                for task in session.query(WorkflowTask).filter_by(
                    workflow_id="9" * 32
                )
            }
            data = tasks[data_task[3]]
            analytics = tasks[analytics_task[3]]
            self.assertEqual(data.agent, "data_intelligence")
            self.assertEqual(data.task_type, "specialist_data_intelligence")
            self.assertEqual(data.status, "completed")
            self.assertEqual(data.output_references[0]["source_fingerprint"], "source-v1")
            self.assertEqual(analytics.agent, "analytics_modelling")
            self.assertEqual(analytics.status, "completed")
            dependency = session.query(chat_api.WorkflowTaskDependency).filter_by(
                task_id=analytics.id
            ).one()
            self.assertEqual(dependency.depends_on_task_id, data.id)

    def test_explicit_recovery_revalidates_and_resumes_failed_specialist(self):
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        objective = "Run EDA on the selected file."
        inspection = {
            "dataset_id": self.dataset_id,
            "file_id": 1,
            "source_fingerprint": "source-v1",
            "profile": {"tables": [{"columns": [{"name": "sales"}]}]},
        }
        evidence = [{"type": "dataset_file_inspection", "source_fingerprint": "source-v1"}]
        arguments = {
            "dataset_id": self.dataset_id,
            "file_id": 1,
            "analysis_type": "univariate",
            "selected_columns": ["sales"],
        }
        material = {
            "objective": objective,
            "operation": "run_eda_analysis",
            "arguments": arguments,
            "inspection": inspection,
            "evidence_references": evidence,
        }
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="7" * 32,
                owner_user_id=self.user_id,
                dataset_id=self.dataset_id,
                idempotency_key="6" * 32,
                objective=objective,
                status="running",
                current_step=0,
                source_scope={
                    "project_id": self.project_id,
                    "dataset_id": self.dataset_id,
                },
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "7" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        failed_task = schedule(
            "specialist_data_intelligence",
            {
                **arguments,
                "requested_operation": "run_eda_analysis",
                "requested_arguments": arguments,
                "stage_input_fingerprint": chat_api._fingerprint_json(material),
                "source_fingerprint": "source-v1",
            },
            lambda: (
                "",
                ToolResult(
                    status="failed",
                    summary="The provider timed out.",
                    error_code="provider_timeout",
                ),
                "data_intelligence",
            ),
            idempotent=True,
            max_retries=1,
            agent="data_intelligence",
        )
        self.assertEqual(failed_task[1].status, "failed")

        class RecoveryRegistry:
            def provider_schemas(self, *, context=None):
                return ()

            def is_engine_operation(self, name):
                return name == "run_eda_analysis"

            def prepare_engine_inspection(self, name, tool_arguments, *, context=None):
                self_name = name
                if self_name != "run_eda_analysis" or tool_arguments != arguments:
                    return ToolResult(
                        status="failed",
                        summary="Unexpected recovery inputs.",
                        error_code="invalid_recovery_inputs",
                    )
                return ToolResult(
                    status="succeeded",
                    summary="Current selected-file inspection.",
                    data=inspection,
                    references=tuple(evidence),
                )

        result = DataIntelligenceResult(
            contract_version=1,
            status="completed",
            summary="The selected sales field is present.",
            observed_fields=["sales"],
            recommended_operation="run_eda_analysis",
            evidence_references=evidence,
        )
        with patch.object(chat_api, "ToolRegistry", RecoveryRegistry), patch.object(
            chat_api, "get_agent_settings", return_value=SimpleNamespace(provider="ollama")
        ), patch.object(chat_api, "create_provider", return_value=object()), patch.object(
            chat_api, "AgentModelRouter", return_value=object()
        ), patch.object(
            chat_api, "run_data_intelligence", return_value=(result, "fixture-model")
        ):
            with self.session_factory() as session:
                workflow = session.get(WorkflowExecution, "7" * 32)
                workflow.status = "failed"
                workflow.updated_at = stale
                task = session.query(WorkflowTask).filter_by(
                    workflow_id="7" * 32
                ).one()
                task.updated_at = stale
                session.commit()
            recovered = self.client.post(
                "/api/v1/chat/workflows/" + "7" * 32 + "/recover"
            )

        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["status"], "partial")
        self.assertEqual(
            recovered.json()["recovery"]["recovered"],
            [failed_task[3]],
        )
        with self.session_factory() as session:
            task = session.query(WorkflowTask).filter_by(
                workflow_id="7" * 32
            ).one()
            self.assertEqual(task.status, "completed")
            handoff = task.result_summary["data"]["handoff"]
            self.assertEqual(handoff["source_fingerprint"], "source-v1")
            self.assertEqual(handoff["output"]["observed_fields"], ["sales"])

    def test_scheduler_identity_tracks_every_multi_dataset_source(self):
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="e" * 32,
                owner_user_id=self.user_id,
                idempotency_key="f" * 32,
                objective="Join selected datasets",
                status="running",
                current_step=0,
            )
            second_dataset = Dataset(
                name="Second analytics source",
                project_id=self.project_id,
                status="Ready",
            )
            session.add_all([workflow, second_dataset])
            session.flush()
            second_file = DatasetFile(
                dataset_id=second_dataset.id,
                filename="second-source.csv",
                storage_key=f"{second_dataset.id}/upload/second-source.csv",
                file_size_bytes=32,
                checksum="a" * 64,
                status="Ready",
            )
            session.add(second_file)
            session.commit()
            second_dataset_id = second_dataset.id
            second_file_id = second_file.id

        schedule = chat_api._workflow_tool_scheduler(
            "e" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        arguments = {
            "sources": [
                {
                    "alias": "selected",
                    "dataset_id": self.dataset_id,
                    "file_id": 1,
                    "table_index": 0,
                },
                {
                    "alias": "other",
                    "dataset_id": second_dataset_id,
                    "file_id": second_file_id,
                    "table_index": 0,
                },
            ],
            "joins": [],
            "dimensions": [],
            "metrics": [],
        }
        executions = []

        def execute():
            executions.append("ran")
            return "{}", ToolResult(
                status="succeeded",
                summary="Multi-dataset result.",
                data={"group_count": 1},
            ), "Analyzing datasets"

        first = schedule(
            "analyze_multiple_datasets",
            arguments,
            execute,
            idempotent=True,
            max_retries=1,
        )
        repeated = schedule(
            "analyze_multiple_datasets",
            arguments,
            execute,
            idempotent=True,
            max_retries=1,
        )
        with self.session_factory() as session:
            source_file = session.get(DatasetFile, second_file_id)
            source_file.checksum = "b" * 64
            source_file.updated_at += timedelta(seconds=1)
            session.commit()
        stale = schedule(
            "analyze_multiple_datasets",
            arguments,
            execute,
            idempotent=True,
            max_retries=1,
        )

        self.assertEqual(executions, ["ran", "ran"])
        self.assertEqual(first[1].data, repeated[1].data)
        self.assertEqual(first[3], repeated[3])
        self.assertNotEqual(repeated[3], stale[3])

    def test_recovery_endpoint_replays_stale_read_and_is_repeat_safe(self):
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="c" * 32,
                owner_user_id=self.user_id,
                dataset_id=self.dataset_id,
                idempotency_key="d" * 32,
                objective="List the selected dataset files",
                status="running",
                current_step=0,
                source_scope={
                    "project_id": self.project_id,
                    "dataset_id": self.dataset_id,
                },
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "c" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        schedule(
            "list_files",
            {"dataset_id": self.dataset_id},
            lambda: (
                "{}",
                ToolResult(
                    status="succeeded",
                    summary="Listed files before interruption.",
                    data=[],
                ),
                "Listing files",
            ),
            idempotent=True,
            max_retries=1,
        )
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, "c" * 32)
            task = session.query(WorkflowTask).filter_by(workflow_id="c" * 32).one()
            workflow.status = "running"
            workflow.updated_at = stale
            task.status = "running"
            task.updated_at = stale
            session.commit()

        executions = []

        class RecoveryRegistry:
            def provider_schemas(self, *, context=None):
                return ({"function": {"name": "list_files"}},)

            def is_engine_operation(self, name):
                return False

            def execute(self, name, arguments, *, context=None):
                executions.append((name, arguments))
                return (
                    "{}",
                    ToolResult(
                        status="succeeded",
                        summary="Recovered file listing.",
                        data=[],
                        references=({"type": "file_listing"},),
                    ),
                    "Listing files",
                )

        with patch.object(chat_api, "ToolRegistry", RecoveryRegistry):
            recovered = self.client.post(
                "/api/v1/chat/workflows/" + "c" * 32 + "/recover"
            )
            repeated = self.client.post(
                "/api/v1/chat/workflows/" + "c" * 32 + "/recover"
            )

        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["status"], "completed")
        recovered_tasks = recovered.json()["recovery"]["recovered"]
        self.assertEqual(len(recovered_tasks), 1)
        self.assertTrue(recovered_tasks[0].startswith("tool:list_files:"))
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["recovery"]["recovered"], [])
        self.assertEqual(len(executions), 1)

        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, "c" * 32)
            dataset = session.get(Dataset, self.dataset_id)
            workflow.status = "running"
            workflow.updated_at = stale
            dataset.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
        with patch.object(chat_api, "ToolRegistry", RecoveryRegistry):
            invalidated = self.client.post(
                "/api/v1/chat/workflows/" + "c" * 32 + "/recover"
            )
        self.assertEqual(invalidated.status_code, 200, invalidated.text)
        self.assertEqual(invalidated.json()["status"], "blocked")
        self.assertEqual(
            len(invalidated.json()["recovery"]["invalidated"]),
            1,
        )
        self.assertEqual(len(executions), 1)

    def test_recovery_preserves_current_completed_non_idempotent_engine_task(self):
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="5" * 32,
                owner_user_id=self.user_id,
                dataset_id=self.dataset_id,
                idempotency_key="4" * 32,
                objective="Run deterministic analysis",
                status="running",
                current_step=0,
                source_scope={
                    "project_id": self.project_id,
                    "dataset_id": self.dataset_id,
                },
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "5" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        executions = []

        def execute():
            executions.append("engine")
            return "{}", ToolResult(
                status="succeeded",
                summary="Deterministic result was persisted.",
                data={"dataset_id": self.dataset_id, "file_id": 1, "rows": 3},
            ), "Run analysis"

        scheduled = schedule(
            "run_eda_analysis",
            {"dataset_id": self.dataset_id, "file_id": 1},
            execute,
            idempotent=False,
        )
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, "5" * 32)
            workflow.status = "running"
            workflow.updated_at = stale
            task = session.query(WorkflowTask).filter_by(
                workflow_id="5" * 32
            ).one()
            task.updated_at = stale
            session.commit()

        class RecoveryRegistry:
            def provider_schemas(self, *, context=None):
                return ({"function": {"name": "run_eda_analysis"}},)

            def is_engine_operation(self, name):
                return name == "run_eda_analysis"

        with patch.object(chat_api, "ToolRegistry", RecoveryRegistry):
            recovered = self.client.post(
                "/api/v1/chat/workflows/" + "5" * 32 + "/recover"
            )

        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["status"], "completed")
        self.assertEqual(recovered.json()["recovery"]["reused"], [scheduled[3]])
        self.assertEqual(executions, ["engine"])
        with self.session_factory() as session:
            task = session.query(WorkflowTask).filter_by(
                workflow_id="5" * 32
            ).one()
            self.assertEqual(task.status, "completed")
            self.assertFalse(task.idempotent)
            self.assertEqual(task.result_summary["data"]["rows"], 3)

    def test_recovery_invalidates_dependent_task_when_upstream_output_changes(self):
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        with self.session_factory() as session:
            workflow = WorkflowExecution(
                id="3" * 32,
                owner_user_id=self.user_id,
                dataset_id=self.dataset_id,
                idempotency_key="2" * 32,
                objective="Inspect selected files",
                status="running",
                current_step=0,
                source_scope={
                    "project_id": self.project_id,
                    "dataset_id": self.dataset_id,
                },
            )
            session.add(workflow)
            session.commit()

        schedule = chat_api._workflow_tool_scheduler(
            "3" * 32,
            self.user_id,
            {"project_id": self.project_id, "dataset_id": self.dataset_id},
        )
        first = schedule(
            "list_files",
            {"dataset_id": self.dataset_id},
            lambda: (
                "{}",
                ToolResult(
                    status="succeeded",
                    summary="Files listed.",
                    data=[{"id": 1}],
                ),
                "Listing files",
            ),
            idempotent=True,
            max_retries=1,
        )
        second = schedule(
            "get_inspection_handoff",
            {"dataset_id": self.dataset_id, "file_id": 1},
            lambda: (
                "{}",
                ToolResult(
                    status="succeeded",
                    summary="Inspection handoff saved.",
                    data={"fingerprint": "upstream-v1"},
                ),
                "Inspecting file",
            ),
            idempotent=True,
            max_retries=1,
            depends_on=[first[3]],
        )
        with self.session_factory() as session:
            workflow = session.get(WorkflowExecution, "3" * 32)
            workflow.status = "running"
            workflow.updated_at = stale
            upstream = session.query(WorkflowTask).filter_by(
                workflow_id="3" * 32,
                task_key=first[3],
            ).one()
            upstream.result_summary = {
                **upstream.result_summary,
                "data": [{"id": 99}],
            }
            session.commit()

        class RecoveryRegistry:
            def provider_schemas(self, *, context=None):
                return (
                    {"function": {"name": "list_files"}},
                    {"function": {"name": "get_inspection_handoff"}},
                )

            def is_engine_operation(self, name):
                return False

        with patch.object(chat_api, "ToolRegistry", RecoveryRegistry):
            recovered = self.client.post(
                "/api/v1/chat/workflows/" + "3" * 32 + "/recover"
            )

        self.assertEqual(recovered.status_code, 200, recovered.text)
        self.assertEqual(recovered.json()["recovery"]["reused"], [first[3]])
        self.assertEqual(recovered.json()["recovery"]["invalidated"], [second[3]])
        with self.session_factory() as session:
            downstream = session.query(WorkflowTask).filter_by(
                workflow_id="3" * 32,
                task_key=second[3],
            ).one()
            self.assertEqual(downstream.status, "blocked")
            self.assertEqual(downstream.error_code, "recovery_source_changed")

    def test_workflow_retrieval_rejects_ambiguous_workspace_ownership(self):
        conversation = self._create_conversation()
        with patch.object(
            chat_api,
            "execute_agent_request",
            side_effect=RuntimeError("must not leak"),
        ):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Inspect the available data"},
            )
        self.assertEqual(response.status_code, 200)

        with self.session_factory() as session:
            session.add(User(email="second-owner@example.invalid"))
            session.commit()

        workflow_response = self.client.get(
            f"/api/v1/chat/workflows/{response.json()['workflow_id']}"
        )
        self.assertEqual(workflow_response.status_code, 409)
        self.assertEqual(
            workflow_response.json()["detail"]["code"],
            "authentication_required",
        )
        with self.session_factory() as session:
            other_owner = session.query(User).filter(
                User.email == "second-owner@example.invalid"
            ).one()
        with patch.object(
            chat_api,
            "_require_workspace_owner",
            return_value=other_owner,
        ):
            unauthorized_recovery = self.client.post(
                f"/api/v1/chat/workflows/{response.json()['workflow_id']}/recover"
            )
        self.assertEqual(unauthorized_recovery.status_code, 404)

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

    def test_empty_model_response_failure_is_saved_with_error_metadata(self):
        conversation = self._create_conversation()
        outcome = AgentOutcome(
            status="failed",
            assistant_text=(
                "The configured model returned an empty response. Retry, and check "
                "the local model if this continues."
            ),
            activities=(),
            structured_results=(),
            warnings=("The configured model returned an empty response.",),
            usage=UsageMetadata(),
            error_code="provider_response_invalid",
        )
        with patch.object(
            chat_api,
            "execute_agent_request",
            return_value=outcome,
        ):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "10+10"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["execution_status"], "failed")
        self.assertEqual(result["error_code"], "provider_response_invalid")
        self.assertIn("returned an empty response", result["assistant_message"]["content"])
        detail = self.client.get(
            f"/api/v1/chat/conversations/{conversation['id']}"
        ).json()
        self.assertEqual(
            detail["messages"][1]["metadata"]["error_code"],
            "provider_response_invalid",
        )

    def test_send_persists_clear_provider_unavailable_error(self):
        conversation = self._create_conversation()
        settings = AgentSettings(
            provider="ollama",
            base_url="http://127.0.0.1:11434",
            model="qwen3:4b-instruct",
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        provider = Mock()
        provider.complete.side_effect = ProviderError(
            "provider_unavailable",
            "The configured model provider is unavailable.",
            retryable=True,
        )
        with patch.object(
            agent_runtime,
            "get_agent_settings",
            return_value=settings,
        ), patch.object(
            agent_runtime,
            "create_provider",
            return_value=provider,
        ):
            response = self.client.post(
                f"/api/v1/chat/conversations/{conversation['id']}/messages",
                json={"content": "Hello"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["execution_status"], "provider_unavailable")
        self.assertEqual(response.json()["error_code"], "provider_unavailable")
        self.assertIn("provider is unavailable", response.json()["assistant_message"]["content"])
        self.assertNotIn("fixture-secret", response.text)

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
            workflow = (
                session.query(WorkflowExecution)
                .filter_by(conversation_id=conversation["id"])
                .one()
            )
            self.assertEqual(workflow.status, "cancelled")
            self.assertEqual(workflow.error_code, "response_superseded")

    def test_provider_status_reports_unconfigured_without_claiming_capabilities(self):
        settings = AgentSettings(
            provider="none",
            base_url=None,
            model=None,
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        with patch.object(chat_api, "get_agent_settings", return_value=settings):
            response = self.client.get("/api/v1/chat/provider-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "not_configured")
        self.assertEqual(response.json()["capabilities"]["text_generation"], "unsupported")
        self.assertIsNone(response.json()["limits"]["context_window_tokens"])

    def test_provider_status_reports_live_health_and_unverified_model_capabilities(self):
        settings = AgentSettings(
            provider="ollama",
            base_url="http://localhost:11434",
            model="fixture-model",
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        provider = Mock()
        provider.check_health.return_value = ProviderHealth(status="healthy")
        with patch.object(chat_api, "get_agent_settings", return_value=settings), patch.object(
            chat_api,
            "create_provider",
            return_value=provider,
        ):
            response = self.client.get("/api/v1/chat/provider-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(
            response.json()["capabilities"]["text_generation"],
            "unverified",
        )
        self.assertEqual(response.json()["capabilities"]["streaming"], "unsupported")
        self.assertEqual(
            response.json()["provider_capabilities"]["streaming"],
            "unsupported",
        )
        self.assertEqual(response.json()["limits"]["max_output_tokens"], 32)
        self.assertNotIn("localhost", response.text)

    def test_provider_status_exposes_identity_and_declared_adapter_capabilities(self):
        settings = AgentSettings(
            provider="ollama",
            base_url="http://localhost:11434",
            model="fixture-model",
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        provider = Mock()
        provider.check_health.return_value = ProviderHealth(
            status="healthy",
            model_identity=ModelIdentity(
                provider="ollama",
                model="fixture-model",
                digest="fixture-digest",
            ),
            capabilities=ProviderCapabilities(
                text_generation="supported",
                tool_calling="supported",
                structured_output="supported",
                streaming="unsupported",
                local_inference="supported",
            ),
        )
        with patch.object(chat_api, "get_agent_settings", return_value=settings), patch.object(
            chat_api,
            "create_provider",
            return_value=provider,
        ):
            response = self.client.get("/api/v1/chat/provider-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["model_identity"],
            {
                "provider": "ollama",
                "model": "fixture-model",
                "digest": "fixture-digest",
            },
        )
        self.assertEqual(
            response.json()["provider_capabilities"]["tool_calling"],
            "supported",
        )
        self.assertEqual(response.json()["capabilities"]["tool_calling"], "unverified")

    def test_provider_status_reports_unavailable_runtime_without_raw_error(self):
        settings = AgentSettings(
            provider="ollama",
            base_url="http://localhost:11434",
            model="fixture-model",
            api_key=None,
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        provider = Mock()
        provider.check_health.side_effect = ProviderError(
            "provider_unavailable",
            "private runtime detail",
        )
        with patch.object(chat_api, "get_agent_settings", return_value=settings), patch.object(
            chat_api,
            "create_provider",
            return_value=provider,
        ):
            response = self.client.get("/api/v1/chat/provider-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertNotIn("private runtime detail", response.text)

    def test_provider_status_sanitizes_health_check_failures(self):
        settings = AgentSettings(
            provider="openai_compatible",
            base_url="https://provider.invalid/v1",
            model="fixture-model",
            api_key="fixture-secret",
            timeout_seconds=1,
            max_output_tokens=32,
            max_model_turns=1,
            max_tool_calls=1,
            max_history_messages=1,
        )
        provider = Mock()
        provider.check_health.side_effect = ProviderError(
            "provider_authentication_failed",
            "fixture-secret",
        )
        with patch.object(chat_api, "get_agent_settings", return_value=settings), patch.object(
            chat_api,
            "create_provider",
            return_value=provider,
        ):
            response = self.client.get("/api/v1/chat/provider-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("fixture-secret", response.text)

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

    def test_analytics_capabilities_route_registered_and_available(self):
        response = self.client.get("/api/v1/chat/analytics/capabilities")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("operations", payload)
        self.assertIn("plan_limits", payload)
        operations = payload["operations"]
        self.assertIsInstance(operations, list)
        self.assertGreater(len(operations), 0)
        first_op = operations[0]
        self.assertIn("operation", first_op)
        self.assertIn("description", first_op)
        self.assertIn("workflow_report_result", first_op)
        self.assertEqual(payload["plan_limits"]["maximum_operations"], 5)
        self.assertEqual(payload["plan_limits"]["maximum_dependency_depth"], 4)

    def test_analytics_capabilities_without_user_returns_conflict(self):
        with self.session_factory() as session:
            session.query(DatasetFile).delete()
            session.query(Dataset).delete()
            session.query(Project).delete()
            session.query(User).delete()
            session.commit()
        response = self.client.get("/api/v1/chat/analytics/capabilities")
        self.assertEqual(response.status_code, 409)
        self.assertIn("workspace", str(response.json()).lower())

    def test_analytics_capabilities_route_prefix_is_chat_not_root(self):
        """Regression: endpoint must be reachable at /api/v1/chat/analytics/capabilities."""
        wrong_path = self.client.get("/analytics/capabilities")
        self.assertEqual(wrong_path.status_code, 404)

        wrong_v1 = self.client.get("/api/v1/analytics/capabilities")
        self.assertNotEqual(
            wrong_v1.status_code,
            200,
            "Capabilities must not be reachable without /chat prefix",
        )

        correct = self.client.get("/api/v1/chat/analytics/capabilities")
        self.assertEqual(correct.status_code, 200)

    def test_analytics_capabilities_409_includes_workspace_profile_code(self):
        """Regression: 409 response must carry workspace_profile_required error code."""
        with self.session_factory() as session:
            session.query(DatasetFile).delete()
            session.query(Dataset).delete()
            session.query(Project).delete()
            session.query(User).delete()
            session.commit()
        response = self.client.get("/api/v1/chat/analytics/capabilities")
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        detail = payload.get("detail", {})
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("code"), "workspace_profile_required")
        self.assertIn("message", detail)


if __name__ == "__main__":
    unittest.main()
