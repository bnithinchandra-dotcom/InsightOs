import hashlib
import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent import langgraph_orchestrator as graph_module
from app.agent.contracts import ToolResult
from app.agent.specialist_agents import (
    AnalyticsDecision,
    DataIntelligenceResult,
    PresentationResult,
    SpecialistAgentError,
)
from app.agent.langgraph_orchestrator import (
    SpecialistGraphServices,
    build_specialist_graph,
    workflow_checkpoint_config,
)


WORKFLOW_ID = "a" * 32
DATASET_ID = 17
FILE_ID = 23
OPERATION = "run_eda_analysis"
SOURCE_FINGERPRINT = "source-v1"
INSPECTION_REFERENCES = [{"type": "inspection", "id": "inspection-1"}]
ENGINE_REFERENCES = [{"type": "engine_result", "id": "result-1"}]
ARGUMENTS = {"dataset_id": DATASET_ID, "file_id": FILE_ID}


def _inspection(fingerprint=SOURCE_FINGERPRINT):
    return {
        "dataset_id": DATASET_ID,
        "file_id": FILE_ID,
        "source_fingerprint": fingerprint,
        "file": {"filename": "bounded.csv", "status": "ready", "tables": []},
        "profile": {
            "tables": [
                {
                    "row_count": 1,
                    "column_count": 1,
                    "columns": [{"name": "amount", "physical_types": ["integer"]}],
                }
            ]
        },
    }


class FakeRegistry:
    def __init__(self):
        self.current_fingerprint = SOURCE_FINGERPRINT
        self.inspection_calls = 0
        self.engine_calls = 0
        self.engine_result = ToolResult(
            status="succeeded",
            summary="EDA completed.",
            data={
                "result_id": "result-1",
                "dataset_id": DATASET_ID,
                "file_id": FILE_ID,
                "result": {
                    "source": {"dataset_id": DATASET_ID, "file_id": FILE_ID}
                },
            },
            references=tuple(ENGINE_REFERENCES),
        )

    def is_engine_operation(self, name):
        return name == OPERATION

    def prepare_engine_inspection(self, name, arguments, *, context=None):
        self.inspection_calls += 1
        if name != OPERATION or arguments != ARGUMENTS:
            return ToolResult(
                status="failed",
                summary="Invalid operation.",
                error_code="invalid_arguments",
            )
        return ToolResult(
            status="succeeded",
            summary="Source inspected.",
            data=_inspection(self.current_fingerprint),
            references=tuple(INSPECTION_REFERENCES),
        )

    def execute(self, name, arguments, *, context=None):
        self.engine_calls += 1
        return json.dumps({"status": "succeeded"}), self.engine_result, "EDA"


class FakePreparation:
    def __init__(self):
        self.handoff_context = FakePreparationContext(previous_stage=None)

    def model_dump(self, mode="json"):
        return {"prepared": True}

    def model_copy(self, update):
        self.handoff_context = update["handoff_context"]
        return self


class FakePreparationContext:
    def __init__(self, *, previous_stage):
        self.previous_stage = previous_stage

    def model_copy(self, update):
        return FakePreparationContext(**update)


class FakePreparationPlanner:
    def prepare(self, **kwargs):
        assert kwargs["source_fingerprint"] == kwargs["inspection"]["source_fingerprint"]
        return FakePreparation()

    def prepare_async(self, **kwargs):
        future = Future()
        future.set_result(self.prepare(**kwargs))
        return future


class FakeScheduler:
    def __init__(
        self,
        *,
        invalidate_specialist_handoffs=False,
        block_analytics_decision=False,
    ):
        self.tasks = {}
        self.calls = []
        self.invalidate_specialist_handoffs = invalidate_specialist_handoffs
        self.block_analytics_decision = block_analytics_decision

    def __call__(
        self,
        task_type,
        arguments,
        execute,
        *,
        idempotent,
        max_retries=0,
        depends_on=None,
        agent="registered_tool",
        task_key=None,
    ):
        depends_on = depends_on or []
        if task_key is None:
            identity = json.dumps(
                [task_type, arguments, depends_on],
                sort_keys=True,
                separators=(",", ":"),
            )
            task_key = f"scheduled:{hashlib.sha256(identity.encode()).hexdigest()[:40]}"
        self.calls.append(
            {
                "task_type": task_type,
                "task_key": task_key,
                "idempotent": idempotent,
                "max_retries": max_retries,
                "depends_on": depends_on,
                "agent": agent,
            }
        )
        if task_key not in self.tasks:
            self.tasks[task_key] = execute()
        raw, result, label = self.tasks[task_key]
        if (
            self.invalidate_specialist_handoffs
            and task_type == "specialist_data_intelligence"
            and isinstance(result.data, dict)
        ):
            result = ToolResult(
                status="succeeded",
                summary=result.summary,
                data={
                    **result.data,
                    "handoff": {**result.data["handoff"], "verified": False},
                },
                references=result.references,
            )
        if (
            self.block_analytics_decision
            and task_type == "specialist_analytics_modelling"
            and isinstance(result.data, dict)
        ):
            handoff = result.data["handoff"]
            result = ToolResult(
                status="succeeded",
                summary=result.summary,
                data={
                    **result.data,
                    "handoff": {
                        **handoff,
                        "output": {**handoff["output"], "status": "blocked"},
                    },
                },
                references=result.references,
            )
        return raw, result, label, task_key


def _services(registry=None, scheduler=None):
    return SpecialistGraphServices(
        router=object(),
        registry=registry or FakeRegistry(),
        workflow_scheduler=scheduler or FakeScheduler(),
        request_context={"dataset_id": DATASET_ID, "project_id": 9},
    )


def _initial_state(**updates):
    state = {
        "workflow_id": WORKFLOW_ID,
        "conversation_id": 44,
        "owner_id": 3,
        "dataset_id": DATASET_ID,
        "user_request": "Inspect the selected data file.",
        "operation_type": OPERATION,
        "arguments": dict(ARGUMENTS),
        "source_scope": {"dataset_id": DATASET_ID, "file_id": FILE_ID},
        "validation_response": '{"claims":[{"claim_type":"count"}]}',
        "validation_structured_results": [
            {"tool": "get_inspection_handoff", "status": "succeeded", "data": {}}
        ],
    }
    state.update(updates)
    return state


def _mock_specialists(monkeypatch, *, fail_at=None, call_log=None):
    calls = call_log if call_log is not None else []

    def run_data_intelligence(router, **kwargs):
        calls.append(("data_intelligence", kwargs))
        if fail_at == "data_intelligence":
            raise SpecialistAgentError("data_intelligence_failed", "Data stage failed.")
        return (
            DataIntelligenceResult(
                contract_version=1,
                status="completed",
                summary="Data profile reviewed.",
                observed_fields=["amount"],
                recommended_operation=OPERATION,
                evidence_references=INSPECTION_REFERENCES,
            ),
            "data-model",
        )

    def run_analytics_decision(router, **kwargs):
        calls.append(("analytics_decision", kwargs))
        if fail_at == "analytics_decision":
            raise SpecialistAgentError("analytics_failed", "Decision failed.")
        return (
            AnalyticsDecision(
                contract_version=1,
                status="approved",
                tool_name=OPERATION,
                arguments=ARGUMENTS,
                summary="Approve the requested EDA.",
                evidence_references=INSPECTION_REFERENCES,
            ),
            "analytics-model",
        )

    def run_presentation(router, **kwargs):
        calls.append(("presentation", kwargs))
        if fail_at == "presentation":
            raise SpecialistAgentError("presentation_failed", "Presentation failed.")
        return (
            PresentationResult(
                contract_version=1,
                status="completed",
                summary="The selected data was analyzed.",
                evidence_references=ENGINE_REFERENCES,
            ),
            "presentation-model",
        )

    monkeypatch.setattr(graph_module, "run_data_intelligence", run_data_intelligence)
    monkeypatch.setattr(graph_module, "run_analytics_decision", run_analytics_decision)
    monkeypatch.setattr(graph_module, "run_presentation", run_presentation)
    monkeypatch.setattr(graph_module, "SPECIALIST_PREPARATION", FakePreparationPlanner())
    return calls


def _invoke(graph, state=None):
    return graph.invoke(state or _initial_state(), workflow_checkpoint_config(WORKFLOW_ID))


def test_graph_builds_required_nodes_and_compiles(monkeypatch):
    _mock_specialists(monkeypatch)
    graph = build_specialist_graph(services=_services())

    assert {
        "validate_input",
        "data_intelligence",
        "analytics_decision",
        "execution_gate",
        "deterministic_bi_engine",
        "claim_and_evidence_validation",
        "presentation_specialist",
    }.issubset(graph.get_graph().nodes)


def test_graph_invokes_existing_specialists_scheduler_validator_and_presentation(
    monkeypatch,
):
    calls = _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler()
    validator_calls = []

    def validate(response, *, structured_results, context):
        validator_calls.append((response, structured_results, context))
        return "validated claims", [{"outcome": "SUPPORTED"}], True

    monkeypatch.setattr(graph_module, "validate_claim_envelope", validate)
    result = _invoke(build_specialist_graph(services=_services(registry, scheduler)))

    assert [name for name, _ in calls] == [
        "data_intelligence",
        "analytics_decision",
        "presentation",
    ]
    assert calls[0][1]["inspection"]["source_fingerprint"] == SOURCE_FINGERPRINT
    assert calls[1][1]["data_intelligence"]["recommended_operation"] == OPERATION
    assert calls[1][1]["preparation"].handoff_context.previous_stage["status"] == "completed"
    assert calls[2][1]["engine_result"]["data"]["result_id"] == "result-1"
    assert validator_calls[0][0] == _initial_state()["validation_response"]
    assert validator_calls[0][2]["dataset_id"] == DATASET_ID
    assert registry.engine_calls == 1
    assert result["execution_status"] == "completed"
    assert result["final_response"]["summary"] == "The selected data was analyzed."
    assert result["analytics_task_reference"] in scheduler.calls[2]["depends_on"]
    assert result["engine_task_reference"] in scheduler.calls[3]["depends_on"]


def test_interrupted_presentation_resumes_without_replaying_engine(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler()
    services = SpecialistGraphServices(
        router=object(),
        registry=registry,
        workflow_scheduler=scheduler,
        request_context={"dataset_id": DATASET_ID, "project_id": 9},
        pause_before_presentation=True,
    )
    graph = build_specialist_graph(
        services=services,
        checkpointer=InMemorySaver(),
    )
    state = _initial_state()
    state.pop("validation_response")
    state.pop("validation_structured_results")
    config = workflow_checkpoint_config(WORKFLOW_ID)

    interrupted = graph.invoke(state, config)

    assert interrupted["__interrupt__"]
    assert registry.engine_calls == 1
    snapshot = graph.get_state(config)
    assert snapshot.next == ("presentation_specialist",)
    assert any(task.interrupts for task in snapshot.tasks)

    resumed = graph.invoke(Command(resume="continue"), config)

    assert resumed["execution_status"] == "completed"
    assert registry.engine_calls == 1
    assert [name for name, _ in calls].count("presentation") == 1


@pytest.mark.parametrize(
    ("fail_at", "expected_error"),
    [
        ("data_intelligence", "data_intelligence_failed"),
        ("analytics_decision", "analytics_failed"),
        ("presentation", "presentation_failed"),
    ],
)
def test_specialist_failures_stop_downstream_stages(monkeypatch, fail_at, expected_error):
    calls = _mock_specialists(monkeypatch, fail_at=fail_at)
    registry = FakeRegistry()
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: ("", [], True),
    )

    result = _invoke(build_specialist_graph(services=_services(registry)))

    assert result["error_code"] == expected_error
    assert registry.engine_calls == (0 if fail_at in {"data_intelligence", "analytics_decision"} else 1)
    if fail_at == "data_intelligence":
        assert "analytics_decision" not in [name for name, _ in calls]
    if fail_at == "analytics_decision":
        assert "presentation" not in [name for name, _ in calls]


def test_invalid_persisted_data_handoff_is_rejected(monkeypatch):
    _mock_specialists(monkeypatch)
    scheduler = FakeScheduler(invalidate_specialist_handoffs=True)
    registry = FakeRegistry()
    result = _invoke(build_specialist_graph(services=_services(registry, scheduler)))

    assert result["error_code"] == "specialist_handoff_invalid"
    assert registry.engine_calls == 0


def test_blocked_persisted_analytics_decision_prevents_engine_execution(monkeypatch):
    _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler(block_analytics_decision=True)

    result = _invoke(build_specialist_graph(services=_services(registry, scheduler)))

    assert result["execution_status"] == "blocked"
    assert result["error_code"] == "analytics_decision_not_executable"
    assert registry.engine_calls == 0


def test_execution_gate_rejects_source_fingerprint_change(monkeypatch):
    _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler()

    def change_after_data_intelligence(name, args, *, context=None):
        registry.inspection_calls += 1
        fingerprint = (
            SOURCE_FINGERPRINT
            if registry.inspection_calls == 1
            else "changed-source"
        )
        return ToolResult(
            status="succeeded",
            summary="Source inspected.",
            data=_inspection(fingerprint),
            references=tuple(INSPECTION_REFERENCES),
        )

    registry.prepare_engine_inspection = change_after_data_intelligence
    result = _invoke(build_specialist_graph(services=_services(registry, scheduler)))

    assert result["error_code"] == "specialist_source_changed"
    assert registry.engine_calls == 0


def test_engine_failure_stops_claim_validation_and_presentation(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    registry.engine_result = ToolResult(
        status="failed",
        summary="Engine failed.",
        error_code="engine_failed",
    )
    validator_calls = []
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: validator_calls.append(args) or ("", [], True),
    )

    result = _invoke(build_specialist_graph(services=_services(registry)))

    assert result["error_code"] == "engine_failed"
    assert validator_calls == []
    assert "presentation" not in [name for name, _ in calls]


def test_engine_result_scope_mismatch_prevents_presentation(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    registry.engine_result = ToolResult(
        status="succeeded",
        summary="EDA completed for an unexpected file.",
        data={
            "dataset_id": DATASET_ID,
            "file_id": FILE_ID + 1,
            "result": {
                "source": {"dataset_id": DATASET_ID, "file_id": FILE_ID + 1}
            },
        },
    )
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: ("", [], True),
    )

    result = _invoke(build_specialist_graph(services=_services(registry)))

    assert result["error_code"] == "engine_result_scope_mismatch"
    assert registry.engine_calls == 1
    assert "presentation" not in [name for name, _ in calls]


def test_rejected_claims_prevent_presentation(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: ("unverified", [{"outcome": "FAILED"}], False),
    )

    result = _invoke(build_specialist_graph(services=_services()))

    assert result["error_code"] == "claim_validation_incomplete"
    assert result["execution_status"] == "blocked"
    assert "presentation" not in [name for name, _ in calls]


def test_duplicate_graph_invocation_reuses_persisted_engine_task(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler()
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: ("validated", [], True),
    )
    graph = build_specialist_graph(services=_services(registry, scheduler))

    first = _invoke(graph)
    second = _invoke(graph)

    engine_calls = [
        call for call in scheduler.calls if call["agent"] == "deterministic_bi_engine"
    ]
    assert len(engine_calls) == 2
    assert engine_calls[0]["task_key"] == engine_calls[1]["task_key"]
    assert engine_calls[0]["idempotent"] is False
    assert engine_calls[0]["max_retries"] == 0
    assert registry.engine_calls == 1
    assert first["engine_task_reference"] == second["engine_task_reference"]
    assert [name for name, _ in calls].count("data_intelligence") == 1
    assert [name for name, _ in calls].count("analytics_decision") == 1
    assert [name for name, _ in calls].count("presentation") == 1


def test_changed_source_generates_a_new_non_replayable_engine_task(monkeypatch):
    _mock_specialists(monkeypatch)
    registry = FakeRegistry()
    scheduler = FakeScheduler()
    monkeypatch.setattr(
        graph_module,
        "validate_claim_envelope",
        lambda *args, **kwargs: ("validated", [], True),
    )
    graph = build_specialist_graph(services=_services(registry, scheduler))
    first = _invoke(graph)

    registry.current_fingerprint = "source-v2"
    result = _invoke(graph)

    engine_calls = [
        call for call in scheduler.calls if call["agent"] == "deterministic_bi_engine"
    ]
    assert first["engine_task_reference"] != result.get("engine_task_reference")
    assert len(engine_calls) == 2
    assert registry.engine_calls == 2
    assert result["execution_status"] == "completed"


def test_malformed_input_stops_before_specialist_invocation(monkeypatch):
    calls = _mock_specialists(monkeypatch)
    result = _invoke(
        build_specialist_graph(services=_services()),
        _initial_state(source_scope={"dataset_id": DATASET_ID, "file_id": -1}),
    )

    assert result["error_code"] == "specialist_input_invalid"
    assert calls == []


def test_checkpoint_configuration_uses_workflow_id_as_thread_id():
    assert workflow_checkpoint_config(WORKFLOW_ID) == {
        "configurable": {"thread_id": WORKFLOW_ID}
    }
