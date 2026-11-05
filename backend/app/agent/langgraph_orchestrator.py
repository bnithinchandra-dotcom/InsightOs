from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.agent.checkpointer import (
    CheckpointerInitializationError,
    _is_checkpointing_required,
)
from app.agent.claim_validation import validate_claim_envelope
from app.agent.contracts import ToolResult
from app.agent.preparation import (
    SPECIALIST_PREPARATION,
    PreparationError,
    SpecialistPreparationArtifact,
)
from app.agent.specialist_agents import (
    AnalyticsDecision,
    DataIntelligenceResult,
    PresentationResult,
    SpecialistAgentError,
    SpecialistHandoff,
    build_specialist_handoff,
    run_analytics_decision,
    run_data_intelligence,
    run_presentation,
    verified_schema_summary,
)
from app.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)
MAX_GRAPH_STATE_BYTES = 256 * 1024
MAX_GRAPH_REFERENCES = 32


class SpecialistGraphState(TypedDict, total=False):
    workflow_id: str
    conversation_id: int | None
    owner_id: int
    dataset_id: int
    user_request: str
    operation_type: str
    arguments: dict[str, Any]
    source_scope: dict[str, Any]
    source_fingerprint: str
    evidence_references: list[dict[str, Any]]
    data_intelligence_result: dict[str, Any]
    data_intelligence_task_reference: str
    analytics_decision: dict[str, Any]
    analytics_task_reference: str
    execution_permitted: bool
    engine_task_reference: str
    engine_result_reference: str
    engine_result: dict[str, Any]
    validation_response: str
    validation_structured_results: list[dict[str, Any]]
    validation_result: dict[str, Any]
    presentation_result: dict[str, Any]
    presentation_task_reference: str
    execution_status: str
    error_code: str
    error_message: str
    node_metadata: dict[str, dict[str, Any]]
    final_response: dict[str, Any]


RequestOrchestrationState = SpecialistGraphState


class _GraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workflow_id: StrictStr = Field(min_length=32, max_length=80)
    conversation_id: StrictInt | None = None
    owner_id: StrictInt = Field(gt=0)
    dataset_id: StrictInt = Field(gt=0)
    user_request: StrictStr = Field(min_length=1, max_length=8_000)
    operation_type: StrictStr = Field(min_length=1, max_length=96)
    arguments: dict[str, Any]
    source_scope: dict[str, Any]
    validation_response: StrictStr = Field(min_length=1, max_length=32 * 1024)
    validation_structured_results: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=10,
    )


WorkflowScheduler = Callable[..., tuple[str, ToolResult, str, str | None]]


@dataclass(frozen=True)
class SpecialistGraphServices:
    router: Any
    registry: ToolRegistry
    workflow_scheduler: WorkflowScheduler
    request_context: dict[str, Any]


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _safe_state_size(state: SpecialistGraphState) -> bool:
    try:
        return _encoded_size(state) <= MAX_GRAPH_STATE_BYTES
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return False


def _failure(
    state: SpecialistGraphState,
    *,
    node: str,
    code: str,
    message: str,
    status: str = "failed",
) -> SpecialistGraphState:
    return {
        "execution_status": status,
        "error_code": code[:64],
        "error_message": message[:500],
        "final_response": {
            "status": status,
            "error_code": code[:64],
            "message": message[:500],
        },
        "node_metadata": {
            **state.get("node_metadata", {}),
            node: {
                "workflow_id": state.get("workflow_id"),
                "node": node,
                "status": status,
                "error_category": code[:64],
            },
        },
    }


def _record_node(
    state: SpecialistGraphState,
    *,
    node: str,
    status: str,
    started: float,
    task_reference: str | None = None,
    error_code: str | None = None,
) -> dict[str, dict[str, Any]]:
    metadata = dict(state.get("node_metadata", {}))
    metadata[node] = {
        "workflow_id": state.get("workflow_id"),
        "node": node,
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "task_reference": task_reference,
        "error_category": error_code,
    }
    logger.info(
        "LangGraph specialist node completed workflow=%s node=%s status=%s error=%s",
        state.get("workflow_id"),
        node,
        status,
        error_code,
    )
    return metadata


def _schedule_specialist(
    services: SpecialistGraphServices,
    *,
    state: SpecialistGraphState,
    role: str,
    operation: str,
    runner: Callable[[], tuple[Any, str]],
    dependency_task_ids: list[str],
    input_material: dict[str, Any],
    parameters: dict[str, Any],
    source_fingerprint: str,
    verified_schema: dict[str, Any] | None = None,
    preparation: SpecialistPreparationArtifact | None = None,
    actual_operations: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    artifact_references: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    stage_fingerprint = hashlib.sha256(
        json.dumps(
            input_material,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    task_arguments: dict[str, Any] = {
        "dataset_id": state["dataset_id"],
        "file_id": state["source_scope"]["file_id"],
        "requested_operation": operation,
        "requested_arguments": parameters,
        "stage_input_fingerprint": stage_fingerprint,
        "source_fingerprint": source_fingerprint,
    }
    if preparation is not None:
        task_arguments["preparation"] = preparation.model_dump(mode="json")

    def execute_stage() -> tuple[str, ToolResult, str]:
        try:
            output, model_name = runner()
        except SpecialistAgentError as error:
            return (
                "",
                ToolResult(
                    status="failed",
                    summary=error.message,
                    error_code=error.code,
                ),
                role,
            )
        _handoff, envelope, reference = build_specialist_handoff(
            stage=role,
            operation=operation,
            source_fingerprint=source_fingerprint,
            dataset_id=state["dataset_id"],
            file_id=state["source_scope"]["file_id"],
            dependency_task_ids=dependency_task_ids,
            output=output,
            model_name=model_name,
            parameters=parameters,
            verified_schema=verified_schema,
            actual_operations=actual_operations,
            warnings=warnings,
            artifact_references=artifact_references,
        )
        result = ToolResult(
            status="succeeded",
            summary=f"{role} completed with a validated handoff.",
            data=envelope,
            references=(reference,),
        )
        raw = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return raw, result, role

    _raw, result, _label, task_key = services.workflow_scheduler(
        f"specialist_{role}",
        task_arguments,
        execute_stage,
        idempotent=True,
        max_retries=1,
        depends_on=dependency_task_ids,
        agent=role,
    )
    if result.status != "succeeded" or not isinstance(result.data, dict):
        raise SpecialistAgentError(
            result.error_code or "specialist_task_failed",
            result.summary or "The specialist stage did not complete.",
        )
    try:
        handoff = SpecialistHandoff.model_validate(result.data.get("handoff"))
        if (
            handoff.stage != role
            or handoff.requested_operation != operation
            or handoff.source_fingerprint != source_fingerprint
            or handoff.dataset_id != state["dataset_id"]
            or handoff.file_id != state["source_scope"]["file_id"]
            or handoff.dependency_task_ids != dependency_task_ids
            or handoff.parameters != parameters
            or handoff.artifact_references
            != handoff.output.get("evidence_references")
            or role == "data_intelligence"
            and not isinstance(handoff.verified_schema, dict)
            or role == "insights_visualization"
            and (
                len(handoff.actual_operations) != 1
                or handoff.actual_operations[0].get("operation") != operation
                or handoff.actual_operations[0].get("status") != "succeeded"
            )
            or not handoff.verified
        ):
            raise ValueError("The scheduled specialist handoff is inconsistent.")
    except (TypeError, ValueError, ValidationError):
        raise SpecialistAgentError(
            "specialist_handoff_invalid",
            "The persisted specialist handoff failed validation.",
        ) from None
    return (
        handoff.model_dump(mode="json"),
        str(result.data.get("model") or ""),
        task_key,
    )


def _engine_result_payload(result: ToolResult) -> dict[str, Any]:
    payload = {
        "status": result.status,
        "summary": result.summary[:1_000],
        "data": result.data,
        "warnings": list(result.warnings[:32]),
        "references": [dict(item) for item in result.references[:MAX_GRAPH_REFERENCES]],
        "limitations": list(result.limitations[:32]),
        "error_code": result.error_code,
    }
    if _encoded_size(payload) > 32 * 1024:
        raise SpecialistAgentError(
            "specialist_engine_result_too_large",
            "The deterministic engine result exceeded the safe graph handoff limit.",
        )
    return payload


def _ensure_checkpointer_available(checkpointer: Any | None) -> None:
    if checkpointer is None and _is_checkpointing_required():
        raise CheckpointerInitializationError(
            "A PostgreSQL checkpointer is required to build a graph in this environment."
        )


def build_specialist_graph(
    *,
    services: SpecialistGraphServices,
    checkpointer: Any | None = None,
) -> Any:
    """Build the specialist workflow using InsightOS specialists and task scheduler."""
    _ensure_checkpointer_available(checkpointer)

    def validate_input(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        try:
            if not _safe_state_size(state):
                raise ValueError("The graph state exceeds the safe size limit.")
            request = _GraphInput.model_validate(
                {key: state.get(key) for key in _GraphInput.model_fields}
            )
            if request.dataset_id != request.source_scope.get("dataset_id"):
                raise ValueError("The source scope does not match the selected dataset.")
            if set(request.source_scope) - {
                "dataset_id",
                "file_id",
                "source_fingerprint",
            }:
                raise ValueError("The source scope contains unsupported fields.")
            if (
                isinstance(request.source_scope.get("file_id"), bool)
                or not isinstance(request.source_scope.get("file_id"), int)
                or request.source_scope["file_id"] < 1
            ):
                raise ValueError("A valid selected file is required.")
            if _encoded_size(request.arguments) > 4096:
                raise ValueError("The operation arguments exceed the safe size limit.")
            if _encoded_size(request.source_scope) > 8 * 1024:
                raise ValueError("The source scope exceeds the safe size limit.")
            if _encoded_size(request.validation_structured_results) > 64 * 1024:
                raise ValueError("The claim evidence exceeds the safe size limit.")
            if not services.registry.is_engine_operation(request.operation_type):
                raise ValueError("The requested operation is not an allowed engine operation.")
        except (
            ValidationError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ):
            return _failure(
                state,
                node="validate_input",
                code="specialist_input_invalid",
                message="The specialist graph input is invalid or outside its selected source scope.",
            )
        result: SpecialistGraphState = {
            "execution_status": "ready",
            "node_metadata": _record_node(
                state,
                node="validate_input",
                status="completed",
                started=started,
            ),
        }
        return result

    def data_intelligence(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        workflow_id = state["workflow_id"]
        operation = state["operation_type"]
        context = dict(services.request_context)
        context["workflow_id"] = workflow_id
        context["user_message"] = state["user_request"]
        inspection_result = services.registry.prepare_engine_inspection(
            operation,
            state["arguments"],
            context=context,
        )
        if (
            inspection_result.status != "succeeded"
            or not isinstance(inspection_result.data, dict)
        ):
            return _failure(
                state,
                node="data_intelligence",
                code=inspection_result.error_code or "specialist_source_unavailable",
                message=inspection_result.summary,
            )
        inspection = inspection_result.data
        source_fingerprint = inspection.get("source_fingerprint")
        dataset_id = inspection.get("dataset_id")
        file_id = inspection.get("file_id")
        if (
            not isinstance(source_fingerprint, str)
            or not source_fingerprint
            or dataset_id != state["dataset_id"]
            or file_id != state["source_scope"]["file_id"]
        ):
            return _failure(
                state,
                node="data_intelligence",
                code="specialist_source_invalid",
                message="The inspected source does not match the selected dataset and file.",
            )
        evidence_references = [
            dict(item)
            for item in inspection_result.references[:MAX_GRAPH_REFERENCES]
            if isinstance(item, dict)
        ]
        if len(inspection_result.references) > MAX_GRAPH_REFERENCES:
            return _failure(
                state,
                node="data_intelligence",
                code="specialist_evidence_too_large",
                message="The source returned more evidence references than allowed.",
            )
        if _encoded_size(inspection) > 48 * 1024:
            return _failure(
                state,
                node="data_intelligence",
                code="specialist_inspection_too_large",
                message="The selected-file inspection exceeded the specialist context limit.",
            )
        try:
            verified_schema = verified_schema_summary(inspection)
            input_material = {
                "objective": state["user_request"],
                "operation": operation,
                "arguments": state["arguments"],
                "inspection": inspection,
                "evidence_references": evidence_references,
            }
            handoff, _model, task_key = _schedule_specialist(
                services,
                state={
                    **state,
                    "source_scope": {
                        **state["source_scope"],
                        "dataset_id": dataset_id,
                        "file_id": file_id,
                    },
                },
                role="data_intelligence",
                operation=operation,
                runner=lambda: run_data_intelligence(
                    services.router,
                    objective=state["user_request"],
                    workflow_id=workflow_id,
                    conversation_id=state.get("conversation_id"),
                    inspection=inspection,
                    operation=operation,
                    evidence_references=evidence_references,
                ),
                dependency_task_ids=[],
                input_material=input_material,
                parameters=state["arguments"],
                source_fingerprint=source_fingerprint,
                verified_schema=verified_schema,
            )
            output = DataIntelligenceResult.model_validate(handoff["output"])
        except SpecialistAgentError as error:
            return _failure(
                state,
                node="data_intelligence",
                code=error.code,
                message=error.message,
            )
        except (TypeError, ValueError, ValidationError, OverflowError):
            return _failure(
                state,
                node="data_intelligence",
                code="specialist_handoff_invalid",
                message="The Data Intelligence output failed its typed contract.",
            )
        result: SpecialistGraphState = {
            "source_fingerprint": source_fingerprint,
            "source_scope": {
                **state["source_scope"],
                "dataset_id": dataset_id,
                "file_id": file_id,
            },
            "evidence_references": evidence_references,
            "data_intelligence_result": output.model_dump(mode="json"),
            "data_intelligence_task_reference": task_key or "",
            "execution_status": "completed",
            "node_metadata": _record_node(
                state,
                node="data_intelligence",
                status="completed",
                started=started,
                task_reference=task_key,
            ),
        }
        if not _safe_state_size({**state, **result}):
            return _failure(
                state,
                node="data_intelligence",
                code="specialist_state_too_large",
                message="The Data Intelligence handoff exceeded the safe graph state limit.",
            )
        return result

    def analytics_decision(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        try:
            data_output = DataIntelligenceResult.model_validate(
                state["data_intelligence_result"]
            )
            context = {
                **services.request_context,
                "workflow_id": state["workflow_id"],
                "user_message": state["user_request"],
            }
            inspection_result = services.registry.prepare_engine_inspection(
                state["operation_type"],
                state["arguments"],
                context=context,
            )
            if (
                inspection_result.status != "succeeded"
                or not isinstance(inspection_result.data, dict)
                or inspection_result.data.get("source_fingerprint")
                != state["source_fingerprint"]
            ):
                raise SpecialistAgentError(
                    "specialist_source_changed",
                    "The selected-file source changed before Analytics Decision.",
                )
            inspection = inspection_result.data
            preparation_future = SPECIALIST_PREPARATION.prepare_async(
                registry=services.registry,
                workflow_id=state["workflow_id"],
                dataset_id=state["dataset_id"],
                file_id=state["source_scope"]["file_id"],
                source_fingerprint=state["source_fingerprint"],
                operation=state["operation_type"],
                inspection=inspection,
                evidence_references=state["evidence_references"],
                context=context,
            )
            try:
                preparation = preparation_future.result(timeout=30)
            except FutureTimeoutError:
                preparation_future.cancel()
                raise SpecialistAgentError(
                    "agent_preparation_timeout",
                    "Deterministic specialist preparation did not finish in time.",
                ) from None
            preparation = preparation.model_copy(
                update={
                    "handoff_context": preparation.handoff_context.model_copy(
                        update={"previous_stage": data_output.model_dump(mode="json")}
                    )
                }
            )
            dependencies = [state["data_intelligence_task_reference"]]
            input_material = {
                "objective": state["user_request"],
                "operation": state["operation_type"],
                "arguments": state["arguments"],
                "inspection": inspection,
                "data_intelligence": data_output.model_dump(mode="json"),
                "preparation": preparation.model_dump(mode="json"),
            }
            handoff, _model, task_key = _schedule_specialist(
                services,
                state=state,
                role="analytics_modelling",
                operation=state["operation_type"],
                runner=lambda: run_analytics_decision(
                    services.router,
                    objective=state["user_request"],
                    workflow_id=state["workflow_id"],
                    conversation_id=state.get("conversation_id"),
                    inspection=input_material["inspection"],
                    data_intelligence=data_output.model_dump(mode="json"),
                    tool_name=state["operation_type"],
                    arguments=state["arguments"],
                    preparation=preparation,
                ),
                dependency_task_ids=dependencies,
                input_material=input_material,
                parameters=state["arguments"],
                source_fingerprint=state["source_fingerprint"],
                preparation=preparation,
            )
            decision = AnalyticsDecision.model_validate(handoff["output"])
        except PreparationError as error:
            return _failure(
                state,
                node="analytics_decision",
                code=error.code,
                message=error.message,
            )
        except SpecialistAgentError as error:
            return _failure(
                state,
                node="analytics_decision",
                code=error.code,
                message=error.message,
            )
        except (TypeError, ValueError, ValidationError, OverflowError):
            return _failure(
                state,
                node="analytics_decision",
                code="specialist_handoff_invalid",
                message="The Analytics Decision output failed its typed contract.",
            )
        result: SpecialistGraphState = {
            "analytics_decision": decision.model_dump(mode="json"),
            "analytics_task_reference": task_key or "",
            "execution_status": "completed",
            "node_metadata": _record_node(
                state,
                node="analytics_decision",
                status="completed",
                started=started,
                task_reference=task_key,
            ),
        }
        if not _safe_state_size({**state, **result}):
            return _failure(
                state,
                node="analytics_decision",
                code="specialist_state_too_large",
                message="The Analytics Decision handoff exceeded the safe graph state limit.",
            )
        return result

    def execution_gate(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        try:
            decision = AnalyticsDecision.model_validate(state["analytics_decision"])
        except (KeyError, TypeError, ValueError, ValidationError):
            return _failure(
                state,
                node="execution_gate",
                code="analytics_decision_invalid",
                message="The Analytics Decision handoff could not be validated.",
            )
        if (
            decision.status != "approved"
            or decision.tool_name != state["operation_type"]
            or decision.arguments != state["arguments"]
            or decision.evidence_references != state["evidence_references"]
            or not state.get("data_intelligence_task_reference")
            or not state.get("analytics_task_reference")
        ):
            return _failure(
                state,
                node="execution_gate",
                code="analytics_decision_not_executable",
                message="The Analytics Decision did not approve the exact requested operation and dependencies.",
                status="blocked",
            )
        context = {
            **services.request_context,
            "workflow_id": state["workflow_id"],
            "user_message": state["user_request"],
        }
        inspection_result = services.registry.prepare_engine_inspection(
            state["operation_type"],
            state["arguments"],
            context=context,
        )
        inspection = inspection_result.data
        if (
            inspection_result.status != "succeeded"
            or not isinstance(inspection, dict)
            or inspection.get("source_fingerprint") != state["source_fingerprint"]
            or inspection.get("dataset_id") != state["dataset_id"]
            or inspection.get("file_id") != state["source_scope"]["file_id"]
        ):
            return _failure(
                state,
                node="execution_gate",
                code="specialist_source_changed",
                message="The selected source changed or became unavailable before engine execution.",
                status="blocked",
            )
        result: SpecialistGraphState = {
            "execution_permitted": True,
            "execution_status": "ready",
            "node_metadata": _record_node(
                state,
                node="execution_gate",
                status="completed",
                started=started,
                task_reference=state["analytics_task_reference"],
            ),
        }
        return result

    def deterministic_bi_engine(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        if not state.get("execution_permitted"):
            return _failure(
                state,
                node="deterministic_bi_engine",
                code="engine_execution_not_permitted",
                message="The deterministic engine was not authorized by the execution gate.",
                status="blocked",
            )
        operation = state["operation_type"]
        arguments = state["arguments"]
        source_fingerprint = state["source_fingerprint"]
        operation_identity = hashlib.sha256(
            json.dumps(
                {
                    "operation": operation,
                    "arguments": arguments,
                    "source_fingerprint": source_fingerprint,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        engine_task_key = f"engine:{operation_identity[:48]}"
        context = {
            **services.request_context,
            "workflow_id": state["workflow_id"],
            "user_message": state["user_request"],
        }

        def execute() -> tuple[str, ToolResult, str]:
            raw, result, label = services.registry.execute(
                operation,
                arguments,
                context=context,
            )
            return raw, result, label

        _raw, engine_result, _label, task_key = services.workflow_scheduler(
            operation,
            arguments,
            execute,
            idempotent=False,
            max_retries=0,
            depends_on=[state["analytics_task_reference"]],
            agent="deterministic_bi_engine",
            task_key=engine_task_key,
        )
        if engine_result.status != "succeeded":
            return _failure(
                state,
                node="deterministic_bi_engine",
                code=engine_result.error_code or "engine_execution_failed",
                message=engine_result.summary,
            )
        try:
            engine_payload = _engine_result_payload(engine_result)
        except SpecialistAgentError as error:
            return _failure(
                state,
                node="deterministic_bi_engine",
                code=error.code,
                message=error.message,
            )
        if not task_key:
            return _failure(
                state,
                node="deterministic_bi_engine",
                code="engine_task_reference_missing",
                message="The scheduled engine result has no durable task reference.",
            )
        result: SpecialistGraphState = {
            "engine_task_reference": task_key,
            "engine_result_reference": task_key,
            "engine_result": engine_payload,
            "execution_status": "completed",
            "node_metadata": _record_node(
                state,
                node="deterministic_bi_engine",
                status="completed",
                started=started,
                task_reference=task_key,
            ),
        }
        if not _safe_state_size({**state, **result}):
            return _failure(
                state,
                node="deterministic_bi_engine",
                code="specialist_state_too_large",
                message="The deterministic engine result exceeded the safe graph state limit.",
            )
        return result

    def claim_and_evidence_validation(
        state: SpecialistGraphState,
    ) -> SpecialistGraphState:
        started = time.monotonic()
        validated_text, decisions, supported = validate_claim_envelope(
            state["validation_response"],
            structured_results=state["validation_structured_results"],
            context={
                **services.request_context,
                "workflow_id": state["workflow_id"],
                "dataset_id": state["dataset_id"],
            },
        )
        result_data = {
            "status": "passed" if supported else "failed",
            "decisions": decisions[:32],
            "validated_text": validated_text[:32 * 1024],
        }
        if not supported:
            failed = _failure(
                state,
                node="claim_and_evidence_validation",
                code="claim_validation_incomplete",
                message="Claims were not fully supported by current structured evidence.",
                status="blocked",
            )
            failed["validation_result"] = result_data
            return failed
        return {
            "validation_result": result_data,
            "execution_status": "completed",
            "node_metadata": _record_node(
                state,
                node="claim_and_evidence_validation",
                status="completed",
                started=started,
            ),
        }

    def presentation_specialist(state: SpecialistGraphState) -> SpecialistGraphState:
        started = time.monotonic()
        if (
            state.get("validation_result", {}).get("status") != "passed"
            or state.get("engine_result", {}).get("status") != "succeeded"
        ):
            return _failure(
                state,
                node="presentation_specialist",
                code="presentation_evidence_unavailable",
                message="Presentation requires a successful engine result and passed claim validation.",
                status="blocked",
            )
        try:
            data_output = DataIntelligenceResult.model_validate(
                state["data_intelligence_result"]
            )
            engine_result = state["engine_result"]
            allowed_references = [
                dict(item)
                for item in engine_result.get("references", [])
                if isinstance(item, dict)
            ]
            dependencies = [
                state["data_intelligence_task_reference"],
                state["analytics_task_reference"],
                state["engine_task_reference"],
            ]
            operation = state["operation_type"]
            operation_fingerprint = hashlib.sha256(
                json.dumps(
                    engine_result,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            handoff, _model, task_key = _schedule_specialist(
                services,
                state=state,
                role="insights_visualization",
                operation=operation,
                runner=lambda: run_presentation(
                    services.router,
                    objective=state["user_request"],
                    workflow_id=state["workflow_id"],
                    conversation_id=state.get("conversation_id"),
                    data_intelligence=data_output.model_dump(mode="json"),
                    engine_result=engine_result,
                    allowed_references=allowed_references,
                ),
                dependency_task_ids=dependencies,
                input_material={
                    "objective": state["user_request"],
                    "data_intelligence": data_output.model_dump(mode="json"),
                    "engine_result": engine_result,
                },
                parameters={
                    "dataset_id": state["dataset_id"],
                    "file_id": state["source_scope"]["file_id"],
                },
                source_fingerprint=state["source_fingerprint"],
                actual_operations=[
                    {
                        "operation": operation,
                        "status": "succeeded",
                        "execution_fingerprint": operation_fingerprint,
                    }
                ],
                warnings=[
                    item
                    for item in engine_result.get("warnings", [])
                    if isinstance(item, str)
                ],
                artifact_references=allowed_references,
            )
            presentation = PresentationResult.model_validate(handoff["output"])
        except SpecialistAgentError as error:
            return _failure(
                state,
                node="presentation_specialist",
                code=error.code,
                message=error.message,
            )
        except (TypeError, ValueError, ValidationError, OverflowError):
            return _failure(
                state,
                node="presentation_specialist",
                code="presentation_result_invalid",
                message="The Presentation Specialist output failed its typed contract.",
            )
        if presentation.status not in {"completed", "partial"}:
            return _failure(
                state,
                node="presentation_specialist",
                code="presentation_not_completed",
                message="The Presentation Specialist did not produce a usable presentation.",
            )
        final_status = "completed" if presentation.status == "completed" else "partial"
        result: SpecialistGraphState = {
            "presentation_result": presentation.model_dump(mode="json"),
            "presentation_task_reference": task_key or "",
            "execution_status": final_status,
            "final_response": {
                "status": final_status,
                "summary": presentation.summary,
                "evidence_references": presentation.evidence_references,
            },
            "node_metadata": _record_node(
                state,
                node="presentation_specialist",
                status=final_status,
                started=started,
                task_reference=task_key,
            ),
        }
        if not _safe_state_size({**state, **result}):
            return _failure(
                state,
                node="presentation_specialist",
                code="specialist_state_too_large",
                message="The presentation result exceeded the safe graph state limit.",
            )
        return result

    builder = StateGraph(SpecialistGraphState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("data_intelligence", data_intelligence)
    builder.add_node("analytics_decision", analytics_decision)
    builder.add_node("execution_gate", execution_gate)
    builder.add_node("deterministic_bi_engine", deterministic_bi_engine)
    builder.add_node("claim_and_evidence_validation", claim_and_evidence_validation)
    builder.add_node("presentation_specialist", presentation_specialist)

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input",
        lambda state: "data_intelligence"
        if state.get("execution_status") == "ready"
        else END,
    )
    builder.add_conditional_edges(
        "data_intelligence",
        lambda state: "analytics_decision"
        if state.get("execution_status") == "completed"
        else END,
    )
    builder.add_conditional_edges(
        "analytics_decision",
        lambda state: "execution_gate"
        if state.get("execution_status") == "completed"
        else END,
    )
    builder.add_conditional_edges(
        "execution_gate",
        lambda state: "deterministic_bi_engine"
        if state.get("execution_permitted")
        else END,
    )
    builder.add_conditional_edges(
        "deterministic_bi_engine",
        lambda state: "claim_and_evidence_validation"
        if state.get("execution_status") == "completed"
        else END,
    )
    builder.add_conditional_edges(
        "claim_and_evidence_validation",
        lambda state: "presentation_specialist"
        if state.get("validation_result", {}).get("status") == "passed"
        else END,
    )
    builder.add_edge("presentation_specialist", END)
    return builder.compile(checkpointer=checkpointer)


def build_request_orchestration_graph(
    *,
    checkpointer: Any | None = None,
    specialist_graph: Any | None = None,
) -> Any:
    """Build the request graph scaffold; live request-path integration is separate."""
    _ensure_checkpointer_available(checkpointer)
    graph = specialist_graph
    builder = StateGraph(SpecialistGraphState)

    def validate_request(state: SpecialistGraphState) -> SpecialistGraphState:
        user_request = state.get("user_request")
        if not isinstance(user_request, str) or not user_request.strip():
            return _failure(
                state,
                node="validate_request",
                code="missing_user_request",
                message="A non-empty user request is required.",
                status="blocked",
            )
        return {"execution_status": "ready"}

    def invoke_specialist(
        state: SpecialistGraphState,
        config: RunnableConfig,
    ) -> SpecialistGraphState:
        if graph is None:
            return _failure(
                state,
                node="specialist_graph",
                code="specialist_graph_not_configured",
                message="The request graph has no specialist graph configured.",
                status="blocked",
            )
        configurable = dict(config.get("configurable") or {})
        configurable["checkpoint_ns"] = "specialist_graph"
        specialist_config = {
            **config,
            "recursion_limit": 10,
            "configurable": configurable,
        }
        return graph.invoke(state, config=specialist_config)

    builder.add_node("validate_request", validate_request)
    builder.add_node("specialist_graph", invoke_specialist)
    builder.add_conditional_edges(
        START,
        lambda state: "validate_request",
    )
    builder.add_conditional_edges(
        "validate_request",
        lambda state: "specialist_graph"
        if state.get("execution_status") == "ready"
        else END,
    )
    builder.add_edge("specialist_graph", END)
    return builder.compile(checkpointer=checkpointer)


def workflow_checkpoint_config(workflow_id: str) -> dict[str, dict[str, str]]:
    if not workflow_id:
        raise ValueError("workflow_id must be non-empty for checkpoint identity.")
    return {"configurable": {"thread_id": workflow_id}}
