from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.agent.claim_validation import validate_claim_envelope
from app.agent.claim_validation import validate_handoff_identity
from app.agent.contracts import (
    AgentExecutionStatus,
    AgentOutcome,
    ToolActivity,
    ToolResult,
    UsageMetadata,
)
from app.agent.tools import ToolRegistry


INSPECTION_CONTRACT_VERSION = 1
MAX_READ_ATTEMPTS = 2
MAX_ORCHESTRATION_STEPS = 6
_INSPECTION_TERMS = {
    "rows": re.compile(r"\b(rows?|records?)\b", re.IGNORECASE),
    "columns": re.compile(r"\b(columns?|fields?)\b", re.IGNORECASE),
    "missing": re.compile(r"\b(missing|null|empty)\b", re.IGNORECASE),
    "types": re.compile(r"\b(types?|schema|data\s+types?)\b", re.IGNORECASE),
    "duplicates": re.compile(r"\bduplicates?|duplicate\s+rows?\b", re.IGNORECASE),
}
_UNSUPPORTED_ANALYTICS = re.compile(
    r"\b(top\s+\d+|sum|total|highest|lowest|trend|forecast|predict|"
    r"prediction|correlation|average|mean|median|anomal(?:y|ies)|"
    r"invalid|suspicious)\b",
    re.IGNORECASE,
)
_EXPLICIT_ENGINE_REQUEST = re.compile(
    r"\b(?:generate|create|make|build|run|perform|execute|calculate|compute)\b"
    r".{0,60}\b(?:monthly\s+)?forecast(?:ing)?\b|"
    r"\b(?:normalize|normalise|run|perform|execute|calculate|compute|analy[sz]e|"
    r"check)\b.{0,60}\b(?:normalization|normalisation|eda|exploratory(?:\s+data\s+analysis)?|"
    r"quality|distribution|statistics|histogram|correlation)\b|"
    r"\b(?:normalize|normalise)\b",
    re.IGNORECASE,
)
_COLUMN_NAMED = re.compile(
    r"\b(?:column|field)\s+(?:named\s+)?[\"']?([A-Za-z_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
_COLUMN_SUFFIX = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_.-]*)\s+(?:column|field)\b",
    re.IGNORECASE,
)
_COLUMN_TARGET = re.compile(
    r"\b(?:in|for|of)\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_.-]*)",
    re.IGNORECASE,
)
_COLUMN_STOP_WORDS = {
    "this",
    "that",
    "the",
    "dataset",
    "file",
    "table",
    "data",
    "values",
    "value",
    "columns",
    "fields",
    "rows",
    "records",
    "all",
    "each",
    "duplicate",
    "duplicates",
    "invalid",
    "missing",
    "null",
    "empty",
    "type",
    "types",
    "contains",
    "contain",
    "have",
    "with",
    "and",
}
_RETRYABLE_TOOL_ERRORS = {
    "tool_execution_failed",
    "task_execution_failed",
    "http_500",
    "http_503",
}


class HandoffContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: Literal[1]
    task_id: StrictStr = Field(min_length=32, max_length=32)
    phase: StrictStr
    role: StrictStr
    status: Literal[
        "completed",
        "partial",
        "failed",
        "blocked",
        "unsupported",
        "skipped",
    ]
    dataset_id: StrictInt | None
    file_id: StrictInt | None
    retry_count: StrictInt = Field(ge=0, le=MAX_READ_ATTEMPTS - 1)
    step: StrictInt = Field(ge=1, le=MAX_ORCHESTRATION_STEPS)
    evidence_references: list[dict[str, Any]]
    summary: StrictStr = Field(max_length=1000)


class WorkflowTaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: StrictStr = Field(min_length=1, max_length=80)
    agent: StrictStr = Field(min_length=1, max_length=64)
    operation: StrictStr = Field(min_length=1, max_length=128)
    depends_on: list[StrictStr] = Field(max_length=8)
    status: Literal[
        "pending",
        "running",
        "completed",
        "partial",
        "failed",
        "blocked",
        "skipped",
        "unsupported",
    ]
    dataset_id: StrictInt | None
    file_id: StrictInt | None
    source_fingerprint: StrictStr | None = None
    input_references: list[dict[str, Any]]
    output_references: list[dict[str, Any]]


@dataclass(frozen=True)
class _InspectionIntent:
    requested_facts: tuple[str, ...]
    unsupported: bool = False
    target_column: str | None = None
    unsupported_topics: tuple[str, ...] = ()


def _target_column(message: str) -> str | None:
    match = _COLUMN_NAMED.search(message)
    if match is None:
        match = _COLUMN_SUFFIX.search(message)
    if match is None:
        match = _COLUMN_TARGET.search(message)
    if match is None:
        return None
    value = match.group(1).strip(" .,:;!?\"'")
    return value if value.casefold() not in _COLUMN_STOP_WORDS else None


def _inspection_intent(message: str) -> _InspectionIntent | None:
    facts = tuple(
        name
        for name, pattern in _INSPECTION_TERMS.items()
        if pattern.search(message)
    )
    if facts:
        unsupported_topics = (
            ("semantic_invalid_value_detection",)
            if re.search(r"\b(invalid|suspicious)\b", message, re.IGNORECASE)
            else ()
        )
        return _InspectionIntent(
            facts,
            target_column=_target_column(message),
            unsupported_topics=unsupported_topics,
        )
    if _UNSUPPORTED_ANALYTICS.search(message):
        return _InspectionIntent((), unsupported=True)
    return None


def _stage(
    *,
    task_id: str,
    phase: str,
    role: str,
    status: str,
    dataset_id: int | None,
    file_id: int | None = None,
    retry_count: int = 0,
    step: int,
    summary: str,
    evidence_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return HandoffContract.model_validate(
        {
            "contract_version": INSPECTION_CONTRACT_VERSION,
            "task_id": task_id,
            "phase": phase,
            "role": role,
            "status": status,
            "dataset_id": dataset_id,
            "file_id": file_id,
            "retry_count": retry_count,
            "step": step,
            "evidence_references": evidence_references or [],
            "summary": summary,
        }
    ).model_dump(mode="json")


def _trace_result(
    *,
    task_id: str,
    dataset_id: int | None,
    status: str,
    stages: list[dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    return {
        "tool": "orchestration_trace",
        "status": "succeeded" if status == "completed" else status,
        "summary": summary,
        "data": {
            "contract_version": INSPECTION_CONTRACT_VERSION,
            "task_id": task_id,
            "dataset_id": dataset_id,
            "outcome": status,
            "max_steps": MAX_ORCHESTRATION_STEPS,
            "max_read_attempts": MAX_READ_ATTEMPTS,
            "stages": stages,
        },
        "warnings": [],
        "references": [],
        "limitations": [
            "The trace is written to durable task records after request execution; intermediate checkpoints are not created."
        ],
    }


def build_engine_workflow_trace(
    *,
    engine_result: dict[str, Any],
    dataset_id: int | None,
    file_id: int | None,
    workflow_id: str,
    conversation_id: int | None,
) -> tuple[dict[str, Any], bool]:
    task_id = workflow_id
    tool_name = engine_result.get("tool")
    result_status = engine_result.get("status")
    data = engine_result.get("data")
    data = data if isinstance(data, dict) else {}
    agent1_inspection = data.get("agent1_inspection")
    agent1_inspection = (
        agent1_inspection if isinstance(agent1_inspection, dict) else {}
    )
    agent1_model_result = data.get("agent1_model_result")
    agent1_model_result = (
        agent1_model_result if isinstance(agent1_model_result, dict) else {}
    )
    agent2_model_result = data.get("agent2_model_result")
    agent2_model_result = (
        agent2_model_result if isinstance(agent2_model_result, dict) else {}
    )
    inspected_file = agent1_inspection.get("file")
    inspected_file = inspected_file if isinstance(inspected_file, dict) else {}
    summary = engine_result.get("summary")
    summary = summary if isinstance(summary, str) else "The engine returned no summary."

    source_matches = (
        isinstance(dataset_id, int)
        and not isinstance(dataset_id, bool)
        and isinstance(file_id, int)
        and not isinstance(file_id, bool)
        and data.get("dataset_id") == dataset_id
        and data.get("file_id") == file_id
        and agent1_inspection.get("status") == "completed"
        and agent1_inspection.get("dataset_id") == dataset_id
        and agent1_inspection.get("file_id") == file_id
        and agent1_model_result.get("status") == "completed"
    )
    if tool_name == "run_eda_analysis":
        result = data.get("result")
        source = result.get("source") if isinstance(result, dict) else None
        source_matches = (
            source_matches
            and isinstance(source, dict)
            and source.get("dataset_id") == dataset_id
            and source.get("file_id") == file_id
        )

    engine_status = data.get("status")
    is_success = result_status == "succeeded"
    is_partial = is_success and engine_status == "PartiallyCompleted"
    verified = (
        is_success
        and source_matches
        and agent2_model_result.get("status") == "approved"
    )
    workflow_status = (
        "failed"
        if not is_success or not source_matches
        else "partial"
        if is_partial
        else "completed"
    )
    evidence_references = [
        item
        for item in engine_result.get("references", [])
        if isinstance(item, dict)
    ] if isinstance(engine_result.get("references"), list) else []
    evidence_references.append(
        {
            "type": "engine_result",
            "tool": tool_name if isinstance(tool_name, str) else "unknown",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "result_id": data.get("id"),
        }
    )
    inspection_references = [
        item
        for item in engine_result.get("references", [])
        if isinstance(item, dict)
        and item.get("type") == "dataset_file_inspection"
    ] if isinstance(engine_result.get("references"), list) else []
    tables = inspected_file.get("tables")
    table_count = len(tables) if isinstance(tables, list) else 0
    inspected_filename = inspected_file.get("filename")
    profile = agent1_inspection.get("profile")
    profile_available = isinstance(profile, dict)
    inspection_succeeded = (
        agent1_inspection.get("status") == "completed"
        and agent1_inspection.get("dataset_id") == dataset_id
        and agent1_inspection.get("file_id") == file_id
        and agent1_model_result.get("status") == "completed"
    )
    engine_succeeded = is_success and source_matches
    verification_status = (
        "completed"
        if verified and not is_partial
        else "partial"
        if verified
        else "blocked"
    )
    data_findings = (
        f"Inspected {inspected_filename} with {table_count} normalized table(s); "
        f"stored profile {'was available' if profile_available else 'was not available'}."
        if isinstance(inspected_filename, str)
        else "Inspected selected file status and available stored profile metadata."
    )

    stages = [
        _stage(
            task_id=task_id,
            phase="planning",
            role="planner_coordinator",
            status="completed",
            dataset_id=dataset_id,
            file_id=file_id,
            step=1,
            summary=f"Dispatched the explicitly requested registered operation: {tool_name}.",
        ),
        _stage(
            task_id=task_id,
            phase="data_understanding",
            role="data_intelligence",
            status="completed" if inspection_succeeded else "failed",
            dataset_id=dataset_id,
            file_id=file_id,
            step=2,
            summary=(data_findings if inspection_succeeded else
                "The selected file inspection or engine output did not verify dataset/file identity."),
            evidence_references=inspection_references,
        ),
        _stage(
            task_id=task_id,
            phase="analytics",
            role="analytics_modelling",
            status=(
                "failed"
                if not is_success
                or not source_matches
                or agent2_model_result.get("status") != "approved"
                else "partial"
                if is_partial
                else "completed"
            ),
            dataset_id=dataset_id,
            file_id=file_id,
            step=3,
            summary=summary[:1000],
            evidence_references=evidence_references,
        ),
        _stage(
            task_id=task_id,
            phase="verification",
            role="verification",
            status=(
                "completed"
                if verified and not is_partial
                else "partial"
                if verified
                else "blocked"
            ),
            dataset_id=dataset_id,
            file_id=file_id,
            step=4,
            summary=(
                "Verified the engine result against the selected data scope."
                if verified
                else "No successful claim was accepted because execution or scope validation failed."
            ),
            evidence_references=evidence_references,
        ),
        _stage(
            task_id=task_id,
            phase="presentation",
            role="insights_visualization",
            status="completed" if verified else "skipped",
            dataset_id=dataset_id,
            file_id=file_id,
            step=5,
            summary=(
                "Prepared the response from the validated structured engine result."
                if verified
                else "Presentation was skipped because verification did not succeed."
            ),
            evidence_references=evidence_references,
        ),
    ]
    trace = _trace_result(
        task_id=task_id,
        dataset_id=dataset_id,
        status=workflow_status,
        stages=stages,
        summary=(
            "The registered engine workflow completed."
            if workflow_status == "completed"
            else "The registered engine workflow returned a partial result."
            if workflow_status == "partial"
            else "The registered engine workflow failed validation or execution."
        ),
    )
    trace["data"]["conversation_id"] = conversation_id
    trace["data"]["file_id"] = file_id
    candidate_fingerprint = agent1_inspection.get("source_fingerprint")
    source_fingerprint = (
        candidate_fingerprint
        if isinstance(candidate_fingerprint, str) and len(candidate_fingerprint) <= 128
        else None
    )
    trace["data"]["source_identity"] = {
        "dataset_id": dataset_id,
        "file_id": file_id,
        "source_fingerprint": source_fingerprint,
    }
    execution_plan = [
        {
            "task_id": f"{task_id}:plan",
            "agent": "planner_coordinator",
            "operation": "dispatch_explicit_engine_request",
            "depends_on": [],
            "status": "completed",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": source_fingerprint,
            "input_references": [],
            "output_references": inspection_references,
        },
        {
            "task_id": f"{task_id}:inspect",
            "agent": "data_intelligence",
            "operation": "inspect_selected_file",
            "depends_on": [f"{task_id}:plan"],
            "status": "completed" if inspection_succeeded else "failed",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": source_fingerprint,
            "input_references": [],
            "output_references": inspection_references,
        },
        {
            "task_id": f"{task_id}:engine",
            "agent": "analytics_modelling",
            "operation": tool_name,
            "depends_on": [f"{task_id}:inspect"],
            "status": (
                "completed"
                if engine_succeeded and not is_partial
                else "partial"
                if engine_succeeded
                else "failed"
            ),
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": source_fingerprint,
            "input_references": inspection_references,
            "output_references": evidence_references,
        },
        {
            "task_id": f"{task_id}:verify",
            "agent": "verification",
            "operation": "validate_source_identity",
            "depends_on": [f"{task_id}:engine"],
            "status": verification_status,
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": source_fingerprint,
            "input_references": evidence_references,
            "output_references": evidence_references if verified else [],
        },
        {
            "task_id": f"{task_id}:present",
            "agent": "insights_visualization",
            "operation": "format_verified_engine_result",
            "depends_on": [f"{task_id}:verify"],
            "status": "completed" if verified else "skipped",
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": source_fingerprint,
            "input_references": evidence_references if verified else [],
            "output_references": [],
        },
    ]
    trace["data"]["execution_plan"] = [
        WorkflowTaskContract.model_validate(task).model_dump(mode="json")
        for task in execution_plan
    ]
    return trace, verified


def _outcome(
    *,
    status: AgentExecutionStatus,
    text: str,
    task_id: str,
    dataset_id: int | None,
    stages: list[dict[str, Any]],
    warnings: tuple[str, ...] = (),
    error_code: str | None = None,
    activities: tuple[ToolActivity, ...] = (),
    structured_results: tuple[dict[str, Any], ...] = (),
    source_identity: dict[str, Any] | None = None,
) -> AgentOutcome:
    trace = _trace_result(
        task_id=task_id,
        dataset_id=dataset_id,
        status=status,
        stages=stages,
        summary=text,
    )
    if source_identity is not None:
        trace["data"]["source_identity"] = source_identity
    return AgentOutcome(
        status=status,
        assistant_text=text,
        activities=activities,
        structured_results=structured_results + (trace,),
        warnings=warnings,
        usage=UsageMetadata(),
        error_code=error_code,
    )


def _execute_read(
    registry: ToolRegistry,
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    workflow_scheduler: Callable[..., tuple[str, ToolResult, str, str | None]] | None = None,
    depends_on: list[str] | None = None,
) -> tuple[ToolResult, str, int, str | None]:
    if workflow_scheduler is not None:
        _output, result, label, task_key = workflow_scheduler(
            tool_name,
            arguments,
            lambda: registry.execute(
                tool_name,
                arguments,
                context=context,
            ),
            idempotent=True,
            max_retries=MAX_READ_ATTEMPTS - 1,
            depends_on=depends_on,
        )
        return result, label, 0, task_key
    attempts = 0
    while attempts < MAX_READ_ATTEMPTS:
        attempts += 1
        _output, result, label = registry.execute(
            tool_name,
            arguments,
            context=context,
        )
        if (
            result.status != "failed"
            or result.error_code not in _RETRYABLE_TOOL_ERRORS
            or attempts == MAX_READ_ATTEMPTS
        ):
            return result, label, attempts - 1, None
    raise RuntimeError("Bounded read operation did not produce a result.")


def _claim(
    *,
    claim_type: str,
    dataset_id: int,
    file_id: int,
    handoff_id: str,
    claim_key: str,
    value: Any,
    unit: str | None,
    denominator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "claim_type": claim_type,
        "dataset_id": dataset_id,
        "file_id": file_id,
        "claim_key": claim_key,
        "value": value,
        "unit": unit,
        "denominator": denominator,
        "evidence_reference": {
            "handoff_id": handoff_id,
            "dataset_id": dataset_id,
            "file_id": file_id,
        },
    }


def _claims_for_intent(
    intent: _InspectionIntent,
    handoff: dict[str, Any],
) -> list[dict[str, Any]]:
    dataset_id = handoff["dataset_id"]
    file_id = handoff["file_id"]
    handoff_id = handoff["handoff_id"]
    claims: list[dict[str, Any]] = []
    for table in handoff["tables"]:
        table_index = table["table_index"]
        if "rows" in intent.requested_facts:
            claims.append(
                _claim(
                    claim_type="count",
                    dataset_id=dataset_id,
                    file_id=file_id,
                    handoff_id=handoff_id,
                    claim_key=f"table[{table_index}].row_count",
                    value=table["row_count"],
                    unit="rows",
                )
            )
        if "columns" in intent.requested_facts:
            claims.append(
                _claim(
                    claim_type="count",
                    dataset_id=dataset_id,
                    file_id=file_id,
                    handoff_id=handoff_id,
                    claim_key=f"table[{table_index}].column_count",
                    value=table["column_count"],
                    unit="columns",
                )
            )
        for column_index, column in enumerate(table["columns"]):
            if (
                intent.target_column is not None
                and column["name"].casefold() != intent.target_column.casefold()
            ):
                continue
            denominator = {"unit": "rows", "count": table["row_count"]}
            if "missing" in intent.requested_facts:
                claims.append(
                    _claim(
                        claim_type="count",
                        dataset_id=dataset_id,
                        file_id=file_id,
                        handoff_id=handoff_id,
                        claim_key=(
                            f"column[{table_index},{column_index}]."
                            "missing_value_count"
                        ),
                        value=column["missing_value_count"],
                        unit="values",
                        denominator=denominator,
                    )
                )
            if "types" in intent.requested_facts:
                claims.append(
                    _claim(
                        claim_type="schema_type",
                        dataset_id=dataset_id,
                        file_id=file_id,
                        handoff_id=handoff_id,
                        claim_key=f"column[{table_index},{column_index}].physical_types",
                        value=column["physical_types"],
                        unit=None,
                    )
                )
        if "duplicates" in intent.requested_facts and table[
            "duplicate_row_count_exact"
        ]:
            claims.append(
                _claim(
                    claim_type="count",
                    dataset_id=dataset_id,
                    file_id=file_id,
                    handoff_id=handoff_id,
                    claim_key=f"table[{table_index}].duplicate_row_count",
                    value=table["duplicate_row_count"],
                    unit="rows",
                    denominator={"unit": "rows", "count": table["row_count"]},
                )
            )
    return claims


def execute_orchestrated_inspection(
    *,
    user_message: str,
    context: dict[str, Any] | None,
    registry: ToolRegistry,
    workflow_scheduler: Callable[..., tuple[str, ToolResult, str, str | None]] | None = None,
) -> AgentOutcome | None:
    if _EXPLICIT_ENGINE_REQUEST.search(user_message):
        return None
    intent = _inspection_intent(user_message)
    if intent is None:
        return None
    registered_tools = {
        item.get("function", {}).get("name")
        for item in registry.provider_schemas()
        if isinstance(item, dict)
        and isinstance(item.get("function"), dict)
    }
    if (
        not intent.unsupported
        and not {"list_files", "get_inspection_handoff"}.issubset(registered_tools)
    ):
        return None

    workflow_id = context.get("workflow_id") if isinstance(context, dict) else None
    task_id = (
        workflow_id
        if isinstance(workflow_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", workflow_id)
        else uuid4().hex
    )
    dataset_id = (
        context.get("dataset_id")
        if isinstance(context, dict)
        and isinstance(context.get("dataset_id"), int)
        and not isinstance(context.get("dataset_id"), bool)
        and context["dataset_id"] > 0
        else None
    )
    stages = [
        _stage(
            task_id=task_id,
            phase="planning",
            role="planner_coordinator",
            status="completed",
            dataset_id=dataset_id,
            step=1,
            summary="Classified the request as a supported dataset inspection.",
        )
    ]
    if intent.unsupported:
        text = (
            "This chat path cannot compute totals, rankings, trends, forecasts, "
            "predictions, correlations, or other new analytical results yet. "
            "It can report stored row/column counts, missing-value counts, "
            "observed types, and exact duplicate counts for one selected dataset file. "
            "It cannot assess semantic invalid or suspicious values."
        )
        stages.extend(
            [
                _stage(
                    task_id=task_id,
                    phase="analytics",
                    role="analytics_modelling",
                    status="unsupported",
                    dataset_id=dataset_id,
                    step=2,
                    summary="No registered chat tool computes the requested metric.",
                ),
                _stage(
                    task_id=task_id,
                    phase="presentation",
                    role="insights_visualization",
                    status="completed",
                    dataset_id=dataset_id,
                    step=3,
                    summary="Returned the unsupported capability explicitly.",
                ),
            ]
        )
        return _outcome(
            status="unsupported",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            warnings=("No analytical tool was run for this request.",),
            error_code="unsupported_capability",
        )

    if dataset_id is None:
        text = "Select a dataset in the chat context before asking for an inspection result."
        stages.append(
            _stage(
                task_id=task_id,
                phase="data_understanding",
                role="data_intelligence",
                status="blocked",
                dataset_id=None,
                step=2,
                summary="Dataset context is required and was not selected.",
            )
        )
        return _outcome(
            status="blocked",
            text=text,
            task_id=task_id,
            dataset_id=None,
            stages=stages,
            error_code="dataset_context_required",
        )

    files_result, files_label, files_retries, files_task_key = _execute_read(
        registry,
        "list_files",
        {"dataset_id": dataset_id},
        context or {},
        workflow_scheduler,
    )
    stages.append(
        _stage(
            task_id=task_id,
            phase="data_understanding",
            role="data_intelligence",
            status=(
                "completed" if files_result.status == "succeeded" else "failed"
            ),
            dataset_id=dataset_id,
            retry_count=files_retries,
            step=2,
            summary=files_result.summary,
        )
    )
    files = files_result.data if isinstance(files_result.data, list) else []
    ready_files = [
        item
        for item in files
        if isinstance(item, dict)
        and item.get("status") == "Ready"
        and isinstance(item.get("id"), int)
        and not isinstance(item.get("id"), bool)
    ]
    if files_result.status != "succeeded":
        text = "Dataset files could not be inspected. Retry the request or check the dataset status."
        return _outcome(
            status="failed",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            warnings=(files_result.summary,),
            error_code=files_result.error_code or "file_lookup_failed",
            activities=(
                ToolActivity(
                    "list_files",
                    files_label,
                    "failed",
                    files_result.summary,
                    files_result.references,
                    files_result.warnings,
                ),
            ),
        )
    if len(ready_files) != 1:
        text = (
            "This inspection path needs exactly one successfully processed file "
            "in the selected dataset. "
            + (
                "No ready file is available yet."
                if not ready_files
                else "More than one ready file is available; select a dataset containing one file."
            )
        )
        stages.append(
            _stage(
                task_id=task_id,
                phase="analytics",
                role="analytics_modelling",
                status="blocked",
                dataset_id=dataset_id,
                step=3,
                summary="A unique ready file could not be resolved.",
            )
        )
        return _outcome(
            status="blocked",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            error_code="file_selection_required",
            activities=(
                ToolActivity(
                    "list_files",
                    files_label,
                    "succeeded",
                    files_result.summary,
                    files_result.references,
                    files_result.warnings,
                ),
            ),
            structured_results=(
                {
                    "tool": "list_files",
                    "status": "succeeded",
                    "summary": files_result.summary,
                    "data": files_result.data,
                    "warnings": list(files_result.warnings),
                    "references": list(files_result.references),
                    "limitations": list(files_result.limitations),
                },
            ),
        )

    file_id = ready_files[0]["id"]
    handoff_result, handoff_label, handoff_retries, _handoff_task_key = _execute_read(
        registry,
        "get_inspection_handoff",
        {"dataset_id": dataset_id, "file_id": file_id},
        context or {},
        workflow_scheduler,
        depends_on=([files_task_key] if files_task_key is not None else None),
    )
    stages.append(
        _stage(
            task_id=task_id,
            phase="data_understanding",
            role="data_intelligence",
            status=(
                "completed" if handoff_result.status == "succeeded" else "failed"
            ),
            dataset_id=dataset_id,
            file_id=file_id,
            retry_count=handoff_retries,
            step=3,
            summary=handoff_result.summary,
        )
    )
    activities = (
        ToolActivity(
            "list_files",
            files_label,
            "succeeded",
            files_result.summary,
            files_result.references,
            files_result.warnings,
        ),
        ToolActivity(
            "get_inspection_handoff",
            handoff_label,
            "succeeded" if handoff_result.status == "succeeded" else "failed",
            handoff_result.summary,
            handoff_result.references,
            handoff_result.warnings,
        ),
    )
    structured_results = (
        {
            "tool": "list_files",
            "status": "succeeded",
            "summary": files_result.summary,
            "data": files_result.data,
            "warnings": list(files_result.warnings),
            "references": list(files_result.references),
            "limitations": list(files_result.limitations),
        },
        {
            "tool": "get_inspection_handoff",
            "status": handoff_result.status,
            "summary": handoff_result.summary,
            "data": handoff_result.data,
            "warnings": list(handoff_result.warnings),
            "references": list(handoff_result.references),
            "limitations": list(handoff_result.limitations),
            "error_code": handoff_result.error_code,
        },
    )
    if handoff_result.status != "succeeded" or not isinstance(
        handoff_result.data, dict
    ):
        text = "The stored inspection evidence could not be validated for this file."
        return _outcome(
            status="partial",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            warnings=(handoff_result.summary,),
            error_code=handoff_result.error_code or "inspection_evidence_unavailable",
            activities=activities,
            structured_results=structured_results,
        )

    handoff = validate_handoff_identity(handoff_result.data)
    if handoff is None:
        text = "The stored inspection evidence failed its identity check and cannot support an answer."
        stages.append(
            _stage(
                task_id=task_id,
                phase="verification",
                role="verification",
                status="failed",
                dataset_id=dataset_id,
                file_id=file_id,
                step=4,
                summary="The inspection handoff identity did not validate.",
            )
        )
        return _outcome(
            status="partial",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            error_code="inspection_handoff_identity_invalid",
            activities=activities,
            structured_results=structured_results,
        )

    if intent.target_column is not None:
        observed_columns = [
            column["name"]
            for table in handoff["tables"]
            for column in table["columns"]
        ]
        if not any(
            name.casefold() == intent.target_column.casefold()
            for name in observed_columns
        ):
            known_columns = ", ".join(dict.fromkeys(observed_columns)) or "none"
            text = (
                f"Column {intent.target_column!r} was not found in the inspected "
                f"file schema. Observed columns: {known_columns}."
            )
            evidence_reference = {
                "handoff_id": handoff["handoff_id"],
                "dataset_id": dataset_id,
                "file_id": file_id,
            }
            stages.extend(
                [
                    _stage(
                        task_id=task_id,
                        phase="analytics",
                        role="analytics_modelling",
                        status="unsupported",
                        dataset_id=dataset_id,
                        file_id=file_id,
                        step=4,
                        summary="The requested column is absent from the inspected schema.",
                        evidence_references=[evidence_reference],
                    ),
                    _stage(
                        task_id=task_id,
                        phase="presentation",
                        role="insights_visualization",
                        status="completed",
                        dataset_id=dataset_id,
                        file_id=file_id,
                        step=5,
                        summary="Reported the schema mismatch without inventing a measurement.",
                        evidence_references=[evidence_reference],
                    ),
                ]
            )
            return _outcome(
                status="unsupported",
                text=text,
                task_id=task_id,
                dataset_id=dataset_id,
                stages=stages,
                warnings=("The requested column is not present in the inspected schema.",),
                error_code="unknown_column",
                activities=activities,
                structured_results=structured_results,
            )

    claims = _claims_for_intent(intent, handoff)
    unavailable_duplicates = (
        "duplicates" in intent.requested_facts
        and any(
            not table["duplicate_row_count_exact"]
            for table in handoff.get("tables", [])
        )
    )
    if not claims:
        text = (
            "The stored profile does not contain enough verified measurements "
            "to answer this inspection request."
        )
        stages.append(
            _stage(
                task_id=task_id,
                phase="analytics",
                role="analytics_modelling",
                status="unsupported",
                dataset_id=dataset_id,
                file_id=file_id,
                step=4,
                summary="No supported measurements were available for the request.",
            )
        )
        return _outcome(
            status="unsupported",
            text=text,
            task_id=task_id,
            dataset_id=dataset_id,
            stages=stages,
            warnings=tuple(handoff.get("unresolved", [])),
            error_code="inspection_measurement_unavailable",
            activities=activities,
            structured_results=structured_results,
        )

    stages.append(
        _stage(
            task_id=task_id,
            phase="analytics",
            role="analytics_modelling",
            status="partial" if intent.unsupported_topics else "completed",
            dataset_id=dataset_id,
            file_id=file_id,
            step=4,
            summary=(
                "Selected stored profile measurements; semantic invalid-value "
                "assessment is unsupported."
                if intent.unsupported_topics
                else "Selected only stored profile measurements requested by the user."
            ),
            evidence_references=handoff.get("evidence_references", []),
        )
    )
    validated_text, decisions, claims_supported = validate_claim_envelope(
        json.dumps({"claims": claims}, ensure_ascii=False, allow_nan=False),
        structured_results=structured_results,
        context=context,
    )
    verification_status = "completed" if claims_supported else "failed"
    stages.append(
        _stage(
            task_id=task_id,
            phase="verification",
            role="verification",
            status=verification_status,
            dataset_id=dataset_id,
            file_id=file_id,
            step=5,
            summary=(
                "Profile claims matched the current validated inspection handoff."
                if claims_supported
                else "At least one requested profile claim could not be validated."
            ),
            evidence_references=[
                {
                    "handoff_id": handoff.get("handoff_id"),
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                }
            ],
        )
    )
    stages.append(
        _stage(
            task_id=task_id,
            phase="presentation",
            role="insights_visualization",
            status="completed" if claims_supported else "partial",
            dataset_id=dataset_id,
            file_id=file_id,
            step=MAX_ORCHESTRATION_STEPS,
            summary="Rendered only the statements produced by claim validation.",
            evidence_references=[
                {
                    "handoff_id": handoff.get("handoff_id"),
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                }
            ],
        )
    )
    claim_validation_result = {
        "tool": "claim_validator",
        "status": "succeeded" if claims_supported else "failed",
        "summary": (
            "Final profile claims were checked against current structured evidence."
            if claims_supported
            else "One or more profile claims could not be verified."
        ),
        "data": {"claims": decisions},
        "warnings": [],
        "references": [],
        "limitations": [
            "Validation covers only the supported structured inspection claim keys."
        ],
    }
    final_status = (
        "completed"
        if claims_supported
        and not unavailable_duplicates
        and not intent.unsupported_topics
        else "partial"
    )
    final_warnings = list(handoff_result.warnings)
    if unavailable_duplicates:
        final_warnings.append(
            "An exact duplicate-row count is unavailable for at least one table."
        )
        validated_text += (
            "\nAn exact duplicate-row count is unavailable for at least one table."
        )
    if intent.unsupported_topics:
        final_warnings.append(
            "Semantic invalid-value detection is not supported by the stored profile."
        )
        validated_text += (
            "\nSemantic invalid-value detection is not supported by this chat path."
        )
    if not claims_supported:
        final_warnings.append(
            "One or more requested facts could not be validated against current evidence."
        )
    return _outcome(
        status=final_status,
        text=validated_text,
        task_id=task_id,
        dataset_id=dataset_id,
        stages=stages,
        warnings=tuple(dict.fromkeys(final_warnings)),
        error_code=(
            "evidence_unavailable"
            if (
                not claims_supported
                or unavailable_duplicates
                or intent.unsupported_topics
            )
            else None
        ),
        activities=activities,
        structured_results=structured_results + (claim_validation_result,),
        source_identity={
            "dataset_id": dataset_id,
            "file_id": file_id,
            "source_fingerprint": handoff.get("source_sha256"),
        },
    )
