from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.checkpointer import (
    CheckpointerInitializationError,
    _is_checkpointing_required,
)
from app.agent.contracts import AgentOutcome
from app.agent.specialist_agents import (
    AnalyticsDecision,
    PresentationResult,
    SpecialistHandoff,
)


class LangGraphState(TypedDict, total=False):
    workflow_id: str | None
    conversation_id: int | str | None
    owner_id: int | None
    dataset_id: int | None
    user_request: str | None
    operation_type: str | None
    source_scope: str | None
    source_fingerprint: str | None
    specialist_handoffs: list[SpecialistHandoff]
    analytics_decision: AnalyticsDecision | None
    engine_task_reference: str | None
    engine_result_reference: str | None
    validation_result: dict[str, Any] | None
    presentation_result: PresentationResult | None
    execution_status: str
    error_code: str | None
    final_response: AgentOutcome | dict[str, Any] | None


RequestOrchestrationState = LangGraphState


def _validate_request(state: LangGraphState) -> LangGraphState:
    user_request = state.get("user_request")
    if not isinstance(user_request, str) or not user_request.strip():
        return {
            "execution_status": "blocked",
            "error_code": "missing_user_request",
            "final_response": {
                "status": "blocked",
                "error_code": "missing_user_request",
            },
        }
    return {"execution_status": "validated"}


def _route_request(state: LangGraphState) -> LangGraphState:
    return {
        "operation_type": state.get("operation_type") or "general",
        "execution_status": "routed",
    }


def _unavailable_result(state: LangGraphState) -> LangGraphState:
    error_code = "langgraph_specialist_pipeline_not_integrated"
    return {
        "execution_status": "blocked",
        "error_code": error_code,
        "final_response": {"status": "blocked", "error_code": error_code},
    }


def _finalize_result(state: LangGraphState) -> LangGraphState:
    if state.get("final_response") is not None:
        return {}
    error_code = state.get("error_code") or "langgraph_request_runtime_not_integrated"
    return {
        "execution_status": "blocked",
        "error_code": error_code,
        "final_response": {"status": "blocked", "error_code": error_code},
    }


def _next_after_validation(
    state: LangGraphState,
) -> Literal["route_request", "finalize_result"]:
    return (
        "route_request"
        if state.get("execution_status") == "validated"
        else "finalize_result"
    )


def _next_after_routing(
    state: LangGraphState,
) -> Literal["specialist_graph", "finalize_result"]:
    return (
        "specialist_graph"
        if state.get("operation_type") != "general"
        else "finalize_result"
    )


def _ensure_checkpointer_available(checkpointer: Any | None) -> None:
    if checkpointer is None and _is_checkpointing_required():
        raise CheckpointerInitializationError(
            "A PostgreSQL checkpointer is required to build a graph in this environment."
        )


def _build_specialist_graph(*, checkpointer: Any | None = None) -> Any:
    builder = StateGraph(LangGraphState)
    builder.add_node("specialist_pipeline_unavailable", _unavailable_result)
    builder.add_edge(START, "specialist_pipeline_unavailable")
    builder.add_edge("specialist_pipeline_unavailable", END)
    return builder.compile(checkpointer=checkpointer)


def build_specialist_graph(*, checkpointer: Any | None = None) -> Any:
    """Build the safe specialist-graph scaffold; specialist stages are not wired yet."""
    _ensure_checkpointer_available(checkpointer)
    return _build_specialist_graph(checkpointer=checkpointer)


def build_request_orchestration_graph(
    *,
    checkpointer: Any | None = None,
    specialist_graph: Any | None = None,
) -> Any:
    """Build the request-graph scaffold with optional persistent checkpointing."""
    _ensure_checkpointer_available(checkpointer)
    graph = specialist_graph or _build_specialist_graph()

    def invoke_specialist_graph(state: LangGraphState) -> LangGraphState:
        return graph.invoke(state, config={"recursion_limit": 10})

    builder = StateGraph(LangGraphState)
    builder.add_node("validate_request", _validate_request)
    builder.add_node("route_request", _route_request)
    builder.add_node("specialist_graph", invoke_specialist_graph)
    builder.add_node("finalize_result", _finalize_result)

    builder.add_edge(START, "validate_request")
    builder.add_conditional_edges("validate_request", _next_after_validation)
    builder.add_conditional_edges("route_request", _next_after_routing)
    builder.add_edge("specialist_graph", "finalize_result")
    builder.add_edge("finalize_result", END)

    return builder.compile(checkpointer=checkpointer)


def workflow_checkpoint_config(workflow_id: str) -> dict[str, dict[str, str]]:
    if not workflow_id:
        raise ValueError("workflow_id must be non-empty for checkpoint identity.")
    return {"configurable": {"thread_id": workflow_id}}
