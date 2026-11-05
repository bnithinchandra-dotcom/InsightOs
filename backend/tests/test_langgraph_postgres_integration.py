import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import Future
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import patch
from uuid import uuid4

import pytest


POSTGRES_DSN_ENV = "INSIGHTOS_TEST_POSTGRES_DSN"
_CHILD_PHASE = "--postgres-checkpoint-phase"


class _CheckpointState(TypedDict, total=False):
    prompt: str
    answer: str


def _checkpoint_graph(checkpointer):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def wait_for_resume(state: _CheckpointState) -> _CheckpointState:
        answer = interrupt({"prompt": state["prompt"]})
        return {"answer": answer}

    builder = StateGraph(_CheckpointState)
    builder.add_node("wait_for_resume", wait_for_resume)
    builder.add_edge(START, "wait_for_resume")
    builder.add_edge("wait_for_resume", END)
    return builder.compile(checkpointer=checkpointer)


def _run_specialist_graph_phase(phase: str, workflow_id: str) -> None:
    from pathlib import Path

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.agent.checkpointer import (
        close_postgres_checkpoint_saver,
        initialize_postgres_checkpoint_saver,
    )
    from app.agent.langgraph_orchestrator import (
        SpecialistGraphServices,
        build_specialist_graph,
    )

    class RejectingRegistry:
        @staticmethod
        def is_engine_operation(_operation: str) -> bool:
            return False

    dsn = os.environ[POSTGRES_DSN_ENV]
    saver = initialize_postgres_checkpoint_saver(dsn, required=True)
    if saver is None:
        raise AssertionError("Required PostgreSQL saver initialization returned None.")
    try:
        services = SpecialistGraphServices(
            router=None,
            registry=RejectingRegistry(),
            workflow_scheduler=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Invalid input must not schedule workflow tasks.")
            ),
            request_context={},
        )
        graph = build_specialist_graph(services=services, checkpointer=saver)
        config = {"configurable": {"thread_id": workflow_id}}

        if phase == "write":
            result = graph.invoke(
                {
                    "workflow_id": workflow_id,
                    "conversation_id": None,
                    "owner_id": 1,
                    "dataset_id": 1,
                    "user_request": "Integration checkpoint verification",
                    "operation_type": "not_an_engine_operation",
                    "arguments": {},
                    "source_scope": {"dataset_id": 1, "file_id": 1},
                    "validation_response": "No operation was executed.",
                    "validation_structured_results": [{"status": "not_executed"}],
                },
                config,
            )
            assert result["error_code"] == "specialist_input_invalid"
            persisted = graph.get_state(config).values
            assert persisted["execution_status"] == "failed"
            assert persisted["error_code"] == "specialist_input_invalid"
        elif phase == "resume":
            persisted = graph.get_state(config).values
            assert persisted["workflow_id"] == workflow_id
            assert persisted["execution_status"] == "failed"
            assert persisted["error_code"] == "specialist_input_invalid"
        else:
            raise ValueError(f"Unknown specialist graph phase: {phase}")
    finally:
        close_postgres_checkpoint_saver()


def _run_live_specialist_recovery_phase(phase: str, workflow_id: str) -> None:
    from pathlib import Path

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.agent import langgraph_orchestrator as graph_module
    from app.agent import runtime as runtime_module
    from app.agent.checkpointer import (
        close_postgres_checkpoint_saver,
        initialize_postgres_checkpoint_saver,
    )
    from app.agent.contracts import ToolResult
    from app.agent.langgraph_orchestrator import (
        SpecialistGraphServices,
        build_specialist_graph,
        workflow_checkpoint_config,
    )
    from app.agent.specialist_agents import (
        AnalyticsDecision,
        DataIntelligenceResult,
        PresentationResult,
    )
    dataset_id = 1
    file_id = 1
    operation = "run_eda_analysis"
    arguments = {"dataset_id": dataset_id, "file_id": file_id}
    source_fingerprint = "postgres-m3-source"
    dsn = os.environ[POSTGRES_DSN_ENV]
    saver = initialize_postgres_checkpoint_saver(dsn, required=True)
    if saver is None:
        raise AssertionError("Required PostgreSQL saver initialization returned None.")

    class Registry:
        def __init__(self):
            self.engine_calls = 0

        @staticmethod
        def is_engine_operation(name):
            return name == operation

        @staticmethod
        def prepare_engine_inspection(name, selected_arguments, *, context=None):
            if name != operation or selected_arguments != arguments:
                return ToolResult(
                    status="failed",
                    summary="Invalid operation.",
                    error_code="invalid_arguments",
                )
            return ToolResult(
                status="succeeded",
                summary="Source inspected.",
                data={
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                    "source_fingerprint": source_fingerprint,
                    "file": {"status": "ready", "tables": []},
                    "profile": {
                        "tables": [
                            {
                                "row_count": 1,
                                "column_count": 1,
                                "columns": [
                                    {
                                        "name": "amount",
                                        "physical_types": ["integer"],
                                    }
                                ],
                            }
                        ]
                    },
                },
            )

        def execute(self, name, selected_arguments, *, context=None):
            self.engine_calls += 1
            return (
                "{}",
                ToolResult(
                    status="succeeded",
                    summary="EDA completed.",
                    data={
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                        "result": {
                            "status": "Completed",
                            "rows_analyzed": 1,
                            "source": {
                                "dataset_id": dataset_id,
                                "file_id": file_id,
                            },
                        },
                    },
                ),
                "EDA",
            )

    class PreparationContext:
        def __init__(self, *, previous_stage=None):
            self.previous_stage = previous_stage

        @staticmethod
        def model_copy(update):
            return PreparationContext(**update)

    class FakePreparation:
        def __init__(self):
            self.handoff_context = PreparationContext()

        @staticmethod
        def model_dump(mode="json"):
            return {"prepared": True}

        def model_copy(self, update):
            self.handoff_context = update["handoff_context"]
            return self

    class PreparationPlanner:
        @staticmethod
        def prepare_async(**_kwargs):
            future = Future()
            future.set_result(FakePreparation())
            return future

    def run_data_intelligence(_router, **_kwargs):
        return (
            DataIntelligenceResult(
                contract_version=1,
                status="completed",
                summary="Source evidence verified.",
                observed_fields=["amount"],
                recommended_operation=operation,
                evidence_references=[],
            ),
            "data-model",
        )

    def run_analytics_decision(_router, **_kwargs):
        return (
            AnalyticsDecision(
                contract_version=1,
                status="approved",
                tool_name=operation,
                arguments=arguments,
                summary="The requested operation was approved.",
                evidence_references=[],
            ),
            "analytics-model",
        )

    def run_presentation(_router, **_kwargs):
        return (
            PresentationResult(
                contract_version=1,
                status="completed",
                summary="Analysis presentation completed.",
                evidence_references=[],
            ),
            "presentation-model",
        )

    class Scheduler:
        def __call__(
            self,
            task_type,
            selected_arguments,
            execute,
            *,
            idempotent,
            max_retries=0,
            depends_on=None,
            agent="registered_tool",
            task_key=None,
        ):
            selected_key = task_key or f"{agent}:{task_type}"
            raw, result, label = execute()
            return raw, result, label, selected_key

    registry = Registry()
    scheduler = Scheduler()
    services = SpecialistGraphServices(
        router=object(),
        registry=registry,
        workflow_scheduler=scheduler,
        request_context={
            "workflow_id": workflow_id,
            "owner_user_id": 1,
            "dataset_id": dataset_id,
        },
        pause_before_presentation=True,
    )
    state = {
        "workflow_id": workflow_id,
        "conversation_id": None,
        "owner_id": 1,
        "dataset_id": dataset_id,
        "user_request": "Run EDA on the selected file.",
        "operation_type": operation,
        "arguments": arguments,
        "source_scope": {"dataset_id": dataset_id, "file_id": file_id},
    }

    try:
        with patch.object(
            graph_module,
            "SPECIALIST_PREPARATION",
            PreparationPlanner(),
        ), patch.object(
            graph_module,
            "run_data_intelligence",
            side_effect=run_data_intelligence,
        ), patch.object(
            graph_module,
            "run_analytics_decision",
            side_effect=run_analytics_decision,
        ), patch.object(
            graph_module,
            "run_presentation",
            side_effect=run_presentation,
        ):
            if phase == "write":
                graph = build_specialist_graph(services=services, checkpointer=saver)
                result = graph.invoke(state, workflow_checkpoint_config(workflow_id))
                assert result["__interrupt__"]
                assert registry.engine_calls == 1
            elif phase == "resume":
                operation_identity = hashlib.sha256(
                    json.dumps(
                        {
                            "operation": operation,
                            "arguments": arguments,
                            "source_fingerprint": source_fingerprint,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                engine_task_reference = f"engine:{operation_identity[:48]}"
                with patch.object(
                    runtime_module,
                    "get_agent_settings",
                    return_value=SimpleNamespace(
                        workflow_token_budget=1_000,
                        workflow_final_reserve_tokens=100,
                    ),
                ), patch.object(
                    runtime_module,
                    "create_provider",
                    return_value=object(),
                ), patch.object(
                    runtime_module,
                    "AgentModelRouter",
                    return_value=object(),
                ), patch.object(
                    runtime_module,
                    "ToolRegistry",
                    return_value=registry,
                ), patch.object(
                    runtime_module,
                    "build_specialist_graph",
                    wraps=build_specialist_graph,
                ):
                    result = runtime_module.resume_interrupted_specialist_graph(
                        workflow_id=workflow_id,
                        owner_user_id=1,
                        dataset_id=dataset_id,
                        context={"dataset_id": dataset_id, "owner_user_id": 1},
                        workflow_scheduler=scheduler,
                        checkpointer=saver,
                        completed_engine_task_references={engine_task_reference},
                    )
                assert result is not None
                assert result["execution_status"] == "completed"
                assert registry.engine_calls == 0
            else:
                raise ValueError(f"Unknown live specialist recovery phase: {phase}")
    finally:
        close_postgres_checkpoint_saver()


def _run_postgres_phase(phase: str, workflow_id: str) -> None:
    from pathlib import Path

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from app.agent.checkpointer import (
        close_postgres_checkpoint_saver,
        initialize_postgres_checkpoint_saver,
    )
    from langgraph.types import Command

    dsn = os.environ[POSTGRES_DSN_ENV]
    saver = initialize_postgres_checkpoint_saver(dsn, required=True)
    if saver is None:
        raise AssertionError("Required PostgreSQL saver initialization returned None.")
    try:
        assert initialize_postgres_checkpoint_saver(dsn, required=True) is saver
        graph = _checkpoint_graph(saver)
        config = {"configurable": {"thread_id": workflow_id}}

        if phase == "write":
            result = graph.invoke({"prompt": "integration checkpoint"}, config)
            assert result["__interrupt__"]
            checkpoint = saver.get_tuple(config)
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["prompt"] == (
                "integration checkpoint"
            )
        elif phase == "resume":
            checkpoint = saver.get_tuple(config)
            assert checkpoint is not None
            assert checkpoint.checkpoint["channel_values"]["prompt"] == (
                "integration checkpoint"
            )
            result = graph.invoke(Command(resume="resumed after process restart"), config)
            assert result["answer"] == "resumed after process restart"
        else:
            raise ValueError(f"Unknown integration phase: {phase}")
    finally:
        close_postgres_checkpoint_saver()


def _run_child_phase(phase: str, workflow_id: str) -> None:
    process = subprocess.run(
        [sys.executable, __file__, _CHILD_PHASE, phase, workflow_id],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        pytest.fail(
            f"Checkpoint subprocess phase {phase!r} failed with exit "
            f"{process.returncode}.\n{process.stderr[-4000:]}"
        )


def test_postgres_checkpoint_survives_process_restart_and_resumes():
    dsn = os.getenv(POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"Set {POSTGRES_DSN_ENV} to run live PostgreSQL integration.")

    workflow_id = f"insightos-m1-checkpoint-{uuid4().hex}"
    _run_child_phase("write", workflow_id)
    _run_child_phase("resume", workflow_id)


def test_specialist_graph_checkpoint_survives_process_restart():
    dsn = os.getenv(POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"Set {POSTGRES_DSN_ENV} to run live PostgreSQL integration.")

    workflow_id = f"insightos-m2-specialist-{uuid4().hex}"
    _run_child_phase("specialist-write", workflow_id)
    _run_child_phase("specialist-resume", workflow_id)


def test_live_specialist_presentation_recovers_without_engine_replay():
    dsn = os.getenv(POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"Set {POSTGRES_DSN_ENV} to run live PostgreSQL integration.")

    workflow_id = uuid4().hex
    _run_child_phase("live-resume-write", workflow_id)
    _run_child_phase("live-resume-resume", workflow_id)


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == _CHILD_PHASE:
    try:
        phase = sys.argv[2]
        if phase.startswith("specialist-"):
            _run_specialist_graph_phase(phase.removeprefix("specialist-"), sys.argv[3])
        elif phase.startswith("live-resume-"):
            _run_live_specialist_recovery_phase(
                phase.removeprefix("live-resume-"),
                sys.argv[3],
            )
        else:
            _run_postgres_phase(phase, sys.argv[3])
    except Exception as exc:
        print(f"PostgreSQL checkpoint phase failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0)
