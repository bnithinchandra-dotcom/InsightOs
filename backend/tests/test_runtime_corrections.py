from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent import api as chat_api
from app.agent.config import AgentSettings
from app.agent.contracts import (
    AgentOutcome,
    ModelResponse,
    ProviderMessage,
    ToolResult,
    UsageMetadata,
)
from app.agent.runtime import (
    _enforce_final_response_policy,
    _trusted_runtime_state,
    execute_agent_request,
)
from app.agent.slim_orchestrator import (
    SlimOrchestratorTurn,
    build_tool_retry_state,
    final_response_length_issue,
    split_sentences,
    tool_operation_identity,
    validate_runtime_turn,
)
from app.database import Base
from app.models import User, WorkflowExecution


@pytest.fixture
def persistent_scheduler(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        user = User(email=f"retry-{uuid4().hex}@example.invalid")
        session.add(user)
        session.flush()
        workflow = WorkflowExecution(
            id=uuid4().hex,
            owner_user_id=user.id,
            idempotency_key=uuid4().hex,
            objective="Exercise persisted scheduler retries",
            status="running",
            current_step=0,
        )
        session.add(workflow)
        session.commit()
        owner_id = user.id
        workflow_id = workflow.id
    monkeypatch.setattr(chat_api, "SessionLocal", session_factory)
    scheduler = chat_api._workflow_tool_scheduler(
        workflow_id,
        owner_id,
        {"project_id": None, "dataset_id": None},
    )
    yield scheduler, session_factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _failure_state(
    *,
    attempts: int | None = 1,
    max_retries: int | None = 1,
    retryable: bool | None = True,
    tool: str = "list_files",
) -> dict[str, Any]:
    result = {
        "tool": tool,
        "status": "failed",
        "scheduler_attempts": attempts,
        "scheduler_max_retries": max_retries,
        "retryable": retryable,
        "scheduler_task_id": "tool:sample",
    }
    return build_tool_retry_state({"operation-identity": [result]})[
        "operation-identity"
    ]


def _turn(tool_name: str, arguments: dict[str, Any]) -> SlimOrchestratorTurn:
    return SlimOrchestratorTurn.model_validate(
        {
            "decision": "continue",
            "intent": "Perform the requested lookup.",
            "plan": [],
            "tool_calls": [{"name": tool_name, "arguments": arguments}],
            "handoff_request": None,
            "response": "I will check the requested information.",
            "clarification_questions": [],
        }
    )


def _validate_turn(
    turn: SlimOrchestratorTurn,
    *,
    retry_state: dict[str, dict[str, Any]] | None = None,
    failed_counts: dict[str, int] | None = None,
    completed: set[str] | None = None,
    identity_resolver=None,
) -> None:
    validate_runtime_turn(
        turn,
        user_message="Please perform the requested lookup.",
        trusted_failed_tool_counts=failed_counts or {},
        prior_verified_profiles={(1, 2)},
        prior_successful_tools=set(),
        trusted_scope_available=True,
        tool_retry_state=retry_state,
        prior_completed_operation_identities=completed,
        tool_identity_resolver=identity_resolver,
    )


def _schedule(scheduler, args, execute, *, max_retries=1):
    return scheduler(
        "list_files",
        args,
        execute,
        idempotent=True,
        max_retries=max_retries,
    )


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, _request):
        return self.responses.pop(0)


def test_retry_state_is_empty_without_trusted_failures():
    assert build_tool_retry_state({}) == {}


@pytest.mark.parametrize(
    ("attempts", "max_retries", "retryable", "expected", "remaining"),
    [
        (1, 1, True, "retry_permitted", 1),
        (2, 1, True, "retry_exhausted", 0),
        (1, 0, True, "retry_exhausted", 0),
        (1, 1, False, "not_retryable", 0),
        (None, 1, True, "unknown", None),
        (1, None, True, "unknown", None),
        (1, 1, None, "unknown", None),
    ],
)
def test_retry_state_distinguishes_eligibility(
    attempts, max_retries, retryable, expected, remaining
):
    state = _failure_state(
        attempts=attempts,
        max_retries=max_retries,
        retryable=retryable,
    )
    assert state["status"] == expected
    assert state["retries_remaining"] == remaining
    assert state["recovery_eligible"] is (expected == "retry_permitted")


def test_missing_retry_metadata_is_unknown_not_exhausted():
    state = _failure_state(attempts=None, max_retries=None, retryable=None)
    assert state["status"] == "unknown"
    assert state["recovery_eligible"] is False
    assert state["retries_remaining"] is None


def test_operation_identity_is_stable_for_same_inputs():
    first = tool_operation_identity("list_files", {"cursor": "a"})
    assert first == tool_operation_identity("list_files", {"cursor": "a"})


@pytest.mark.parametrize(
    ("first_tool", "first_args", "second_tool", "second_args", "context_a", "context_b"),
    [
        ("list_files", {"cursor": "a"}, "list_files", {"cursor": "b"}, {}, {}),
        ("list_files", {}, "get_profile", {}, {}, {}),
        (
            "list_files",
            {},
            "list_files",
            {},
            {"source_fingerprint": "source-a"},
            {"source_fingerprint": "source-b"},
        ),
    ],
)
def test_operation_identity_separates_independent_inputs(
    first_tool,
    first_args,
    second_tool,
    second_args,
    context_a,
    context_b,
):
    assert tool_operation_identity(
        first_tool, first_args, context_a
    ) != tool_operation_identity(second_tool, second_args, context_b)


def test_runtime_state_exposes_persisted_retry_metadata():
    identity = tool_operation_identity("list_files", {"cursor": "x"})
    result = {
        "tool": "list_files",
        "status": "failed",
        "retryable": True,
        "scheduler_attempts": 1,
        "scheduler_max_retries": 1,
        "scheduler_task_id": "tool:stable",
        "scheduler_workflow_id": uuid4().hex,
        "retry_identity": identity,
    }
    state = _trusted_runtime_state([], [result])
    assert state["tool_retry_state"][identity]["status"] == "retry_permitted"
    assert state["tool_retry_state"][identity]["scheduler_task_id"] == "tool:stable"
    assert state["prior_failed_tool_counts"] == {"list_files": 1}


def test_runtime_state_marks_failure_without_identity_unknown():
    state = _trusted_runtime_state(
        [],
        [{"tool": "list_files", "status": "failed"}],
    )
    assert state["tool_retry_state"]["unknown:list_files"]["status"] == "unknown"
    assert state["tool_retry_state"]["unknown:list_files"]["recovery_eligible"] is False


def test_later_success_clears_the_operation_failure_state():
    identity = tool_operation_identity("list_files", {"cursor": "x"})
    old_failure = {
        "tool": "list_files",
        "status": "failed",
        "retry_identity": identity,
        "retryable": True,
        "scheduler_attempts": 1,
        "scheduler_max_retries": 1,
    }
    success = {
        **old_failure,
        "status": "succeeded",
        "data": {"files": []},
    }
    state = _trusted_runtime_state(
        [{"role": "assistant", "metadata": {"structured_results": [old_failure]}}],
        [success],
    )
    assert identity not in state["tool_retry_state"]


def test_completed_operation_is_protected_for_its_workflow():
    workflow_id = uuid4().hex
    identity = tool_operation_identity(
        "run_quality_analysis",
        {"dataset_id": 1, "file_id": 2},
        {"dataset_id": 1},
    )
    result = {
        "tool": "run_quality_analysis",
        "status": "succeeded",
        "retry_identity": identity,
        "scheduler_workflow_id": workflow_id,
    }
    current = _trusted_runtime_state(
        [{"role": "assistant", "metadata": {"structured_results": [result]}}],
        [],
        protected_workflow_ids={workflow_id},
    )
    unrelated = _trusted_runtime_state(
        [{"role": "assistant", "metadata": {"structured_results": [result]}}],
        [],
        protected_workflow_ids={uuid4().hex},
    )
    assert current["prior_completed_operation_identities"] == [identity]
    assert unrelated["prior_completed_operation_identities"] == []


def test_completed_engine_operation_cannot_be_replayed():
    args = {"dataset_id": 1, "file_id": 2}
    identity = tool_operation_identity(
        "run_quality_analysis",
        args,
        {"dataset_id": 1},
    )
    with pytest.raises(ValueError, match="cannot be replayed"):
        validate_runtime_turn(
            _turn("run_quality_analysis", args),
            user_message="Please run quality analysis for the selected dataset.",
            trusted_failed_tool_counts={},
            prior_verified_profiles={(1, 2)},
            prior_successful_tools=set(),
            trusted_scope_available=True,
            prior_completed_operation_identities={identity},
            tool_identity_resolver=lambda _proposal: identity,
        )


def test_retry_validator_allows_one_retry_for_matching_identity():
    args = {"cursor": "same"}
    identity = tool_operation_identity("list_files", args)
    state = {
        identity: {
            "status": "retry_permitted",
            "retries_remaining": 1,
            "recovery_tools": [],
        }
    }
    _validate_turn(
        _turn("list_files", args),
        retry_state=state,
        failed_counts={"list_files": 1},
        identity_resolver=lambda _proposal: identity,
    )


def test_retry_validator_rejects_unknown_eligibility_safely():
    args = {"cursor": "same"}
    identity = tool_operation_identity("list_files", args)
    state = {
        identity: {
            "status": "unknown",
            "retries_remaining": None,
            "recovery_tools": [],
        }
    }
    with pytest.raises(ValueError, match="eligibility is unknown"):
        _validate_turn(
            _turn("list_files", args),
            retry_state=state,
            failed_counts={"list_files": 1},
            identity_resolver=lambda _proposal: identity,
        )


def test_retry_validator_does_not_share_budget_between_inputs():
    failed_args = {"cursor": "failed"}
    candidate_args = {"cursor": "new"}
    failed_identity = tool_operation_identity("list_files", failed_args)
    state = {
        failed_identity: {
            "status": "retry_exhausted",
            "retries_remaining": 0,
            "recovery_tools": [],
        }
    }
    _validate_turn(
        _turn("list_files", candidate_args),
        retry_state=state,
        failed_counts={"list_files": 1},
        identity_resolver=lambda _proposal: tool_operation_identity(
            "list_files", candidate_args
        ),
    )


def test_retry_validator_uses_conservative_unknown_for_ambiguous_identity():
    with pytest.raises(ValueError, match="unknown"):
        _validate_turn(
            _turn("list_files", {"cursor": "new"}),
            retry_state={
                "unknown:list_files": {
                    "status": "unknown",
                    "retries_remaining": None,
                }
            },
            failed_counts={"list_files": 1},
        )


def test_scheduler_retries_retryable_operation_and_returns_persisted_attempts(
    persistent_scheduler,
):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def execute():
        calls.append(1)
        if len(calls) == 1:
            return (
                "temporary failure",
                ToolResult(
                    status="failed",
                    summary="The read failed temporarily.",
                    error_code="temporary_failure",
                    retryable=True,
                ),
                "Files",
            )
        return "ok", ToolResult(status="succeeded", summary="Files loaded."), "Files"

    _raw, result, _label, task_id = _schedule(scheduler, {"cursor": "a"}, execute)
    assert len(calls) == 2
    assert result.status == "succeeded"
    assert result.scheduler_attempts == 2
    assert result.scheduler_max_retries == 1
    assert result.scheduler_task_id == task_id
    assert result.retry_identity == tool_operation_identity(
        "list_files", {"cursor": "a"}, {"project_id": None, "dataset_id": None}
    )


def test_scheduler_does_not_retry_non_retryable_failure(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def execute():
        calls.append(1)
        return (
            "failed",
            ToolResult(
                status="failed",
                summary="The request is invalid.",
                error_code="invalid_arguments",
                retryable=False,
            ),
            "Files",
        )

    _raw, result, _label, _task_id = _schedule(scheduler, {}, execute)
    assert len(calls) == 1
    assert result.retryable is False
    assert result.scheduler_attempts == 1
    assert build_tool_retry_state(
        {
            result.retry_identity: [
                {
                    "tool": "list_files",
                    "status": "failed",
                    "retryable": result.retryable,
                    "scheduler_attempts": result.scheduler_attempts,
                    "scheduler_max_retries": result.scheduler_max_retries,
                }
            ]
        }
    )[result.retry_identity]["status"] == "not_retryable"


def test_scheduler_missing_retryability_stays_unknown(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def execute():
        calls.append(1)
        return (
            "failed",
            ToolResult(status="failed", summary="No retry classification."),
            "Files",
        )

    _raw, result, _label, _task_id = _schedule(scheduler, {}, execute)
    assert len(calls) == 1
    assert result.retryable is None
    assert result.scheduler_attempts == 1
    state = build_tool_retry_state(
        {
            result.retry_identity: [
                {
                    "tool": "list_files",
                    "status": "failed",
                    "retryable": result.retryable,
                    "scheduler_attempts": result.scheduler_attempts,
                    "scheduler_max_retries": result.scheduler_max_retries,
                }
            ]
        }
    )
    assert state[result.retry_identity]["status"] == "unknown"


def test_scheduler_zero_retry_budget_is_exhausted(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def execute():
        calls.append(1)
        return (
            "failed",
            ToolResult(status="failed", summary="Retryable.", retryable=True),
            "Files",
        )

    _raw, result, _label, _task_id = _schedule(
        scheduler,
        {},
        execute,
        max_retries=0,
    )
    assert len(calls) == 1
    assert result.scheduler_attempts == 1
    assert result.scheduler_max_retries == 0
    assert _failure_state(
        attempts=result.scheduler_attempts,
        max_retries=result.scheduler_max_retries,
        retryable=result.retryable,
    )["status"] == "retry_exhausted"


def test_scheduler_exhaustion_persists_across_reentry(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def always_fails():
        calls.append(1)
        return (
            "failed",
            ToolResult(
                status="failed",
                summary="Still unavailable.",
                error_code="unavailable",
                retryable=True,
            ),
            "Files",
        )

    first = _schedule(scheduler, {"cursor": "same"}, always_fails)
    second = _schedule(
        scheduler,
        {"cursor": "same"},
        lambda: (
            calls.append(1)
            or ("unexpected", ToolResult(status="succeeded", summary=""), "Files")
        ),
    )
    assert len(calls) == 2
    assert first[1].scheduler_attempts == 2
    assert second[1].scheduler_attempts == 2
    assert second[1].scheduler_task_id == first[1].scheduler_task_id


def test_scheduler_completed_task_is_not_replayed(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler
    calls = []

    def succeeds():
        calls.append(1)
        return "ok", ToolResult(status="succeeded", summary="Files loaded."), "Files"

    first = _schedule(scheduler, {"cursor": "same"}, succeeds)
    second = _schedule(
        scheduler,
        {"cursor": "same"},
        lambda: (
            calls.append(1)
            or ("replayed", ToolResult(status="succeeded", summary="Replay"), "Files")
        ),
    )
    assert len(calls) == 1
    assert first[3] == second[3]
    assert second[1].summary == "Files loaded."
    assert second[1].scheduler_attempts == 1


def test_scheduler_gives_same_tool_independent_task_ids(persistent_scheduler):
    scheduler, _session_factory = persistent_scheduler

    def fails(args):
        return lambda: (
            "failed",
            ToolResult(
                status="failed",
                summary=f"Failed {args['cursor']}.",
                retryable=False,
            ),
            "Files",
        )

    first = _schedule(scheduler, {"cursor": "a"}, fails({"cursor": "a"}))
    second = _schedule(scheduler, {"cursor": "b"}, fails({"cursor": "b"}))
    assert first[3] != second[3]
    assert first[1].retry_identity != second[1].retry_identity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dr. Smith saw 3.14 units. E.g. the measure was stable.", 2),
        ("The U.S. result is useful. A.M. output is separate.", 2),
        ("First. second. Third.", 3),
        ("One. Two. Three. Four.", 4),
    ],
)
def test_sentence_splitter_handles_abbreviations_decimals_and_case(text, expected):
    assert len(split_sentences(text)) == expected


def test_final_response_length_policy_checks_sentence_and_character_limits():
    assert final_response_length_issue("One concise sentence.") is None
    assert final_response_length_issue("One. Two. Three. Four.") is not None
    assert final_response_length_issue("x" * 901) is not None


def test_response_policy_preserves_warning_and_result_when_shortening():
    outcome = AgentOutcome(
        status="completed",
        assistant_text=" ".join(
            f"Sentence {index} reports a detailed result."
            for index in range(1, 30)
        ),
        activities=(),
        structured_results=(
            {"tool": "run_quality_analysis", "summary": "Quality report completed."},
        ),
        warnings=("Critical warning: two records need review.",),
        usage=UsageMetadata(),
    )
    corrected = _enforce_final_response_policy(outcome)
    assert corrected.status == "partial"
    assert final_response_length_issue(corrected.assistant_text) is None
    assert "Critical warning" in corrected.assistant_text
    assert "Quality report completed" in corrected.assistant_text
    assert any("shortened" in warning for warning in corrected.warnings)


@pytest.mark.parametrize("status", ["completed", "blocked", "failed"])
def test_response_policy_applies_to_success_clarification_and_error_paths(status):
    outcome = AgentOutcome(
        status=status,
        assistant_text="Too long. " * 120,
        activities=(),
        structured_results=(
            {
                "type": "pending_clarification",
                "questions": ["Which reporting period should I use?"],
            },
        )
        if status == "blocked"
        else (),
        warnings=(),
        usage=UsageMetadata(),
    )
    corrected = _enforce_final_response_policy(outcome)
    assert final_response_length_issue(corrected.assistant_text) is None
    if status != "completed":
        assert corrected.status == status


def test_response_policy_leaves_valid_text_unchanged():
    outcome = AgentOutcome(
        status="completed",
        assistant_text="The report is ready.",
        activities=(),
        structured_results=(),
        warnings=(),
        usage=UsageMetadata(),
    )
    assert _enforce_final_response_policy(outcome) is outcome


def test_response_policy_uses_safe_fallback_for_unbroken_oversized_text():
    outcome = AgentOutcome(
        status="failed",
        assistant_text="x" * 2_000,
        activities=(),
        structured_results=(),
        warnings=(),
        usage=UsageMetadata(),
    )
    corrected = _enforce_final_response_policy(outcome)
    assert len(corrected.assistant_text) <= 900
    assert 1 <= len(split_sentences(corrected.assistant_text)) <= 3


def test_public_runtime_enforces_final_response_length_on_model_output():
    settings = AgentSettings(
        provider="ollama",
        base_url="http://provider.invalid",
        model="qwen3:8b",
        api_key=None,
        timeout_seconds=1,
        max_output_tokens=64,
        max_model_turns=3,
        max_tool_calls=2,
        max_history_messages=4,
    )
    provider = SequenceProvider(
        [
            ModelResponse(
                ProviderMessage(
                    role="assistant",
                    content=" ".join(
                        f"Result {index} is available."
                        for index in range(40)
                    ),
                ),
                finish_reason="stop",
            )
        ]
    )
    outcome = execute_agent_request(
        user_message="Explain a general topic.",
        history=[],
        settings=settings,
        provider=provider,
    )
    assert outcome.status == "partial"
    assert final_response_length_issue(outcome.assistant_text) is None


def test_pending_workflow_completed_results_remain_protected_on_clarification():
    workflow_id = uuid4().hex
    identity = tool_operation_identity(
        "run_quality_analysis",
        {"dataset_id": 1, "file_id": 2},
        {"dataset_id": 1},
    )
    state = _trusted_runtime_state(
        [
            {
                "role": "assistant",
                "metadata": {
                    "structured_results": [
                        {
                            "tool": "run_quality_analysis",
                            "status": "succeeded",
                            "retry_identity": identity,
                            "scheduler_workflow_id": workflow_id,
                        }
                    ]
                },
            }
        ],
        [],
        protected_workflow_ids={uuid4().hex, workflow_id},
    )
    assert identity in state["prior_completed_operation_identities"]
