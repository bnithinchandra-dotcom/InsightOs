from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.agent.config import MAX_TOOL_OUTPUT_BYTES
from app.agent.contracts import ToolExecutionError, ToolResult
from app.task_execution import (
    CapabilityRegistry,
    ExecutionContext,
    TaskInput,
    TaskRequirements,
    backend_agent_registry,
    run_task,
)


logger = logging.getLogger(__name__)


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyArguments(StrictArguments):
    pass


class ProjectArguments(StrictArguments):
    project_id: StrictInt = Field(gt=0)


class DatasetArguments(StrictArguments):
    dataset_id: StrictInt = Field(gt=0)


class FileArguments(DatasetArguments):
    file_id: StrictInt = Field(gt=0)


class EDAAnalysisArguments(FileArguments):
    analysis_id: StrictInt = Field(gt=0)


class SchemaAnalysisIdArguments(DatasetArguments):
    analysis_id: StrictInt = Field(gt=0)


class SchemaHistoryArguments(DatasetArguments):
    limit: StrictInt = Field(default=10, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0)


class EDAHistoryArguments(FileArguments):
    limit: StrictInt = Field(default=10, ge=1, le=100)
    offset: StrictInt = Field(default=0, ge=0)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str
    arguments_model: type[StrictArguments]
    execute: Callable[[dict[str, Any]], ToolResult]

    def provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


def _bound(data: Any, name: str) -> Any:
    try:
        serialized = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        size = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        raise ToolExecutionError(
            "tool_result_invalid",
            f"{name} returned data that could not be safely displayed.",
        ) from None
    if size > MAX_TOOL_OUTPUT_BYTES:
        raise ToolExecutionError(
            "tool_result_too_large",
            f"{name} returned more data than can be safely included in this conversation.",
        )
    return data


def _clean_file(file_record: dict[str, Any]) -> dict[str, Any]:
    normalization = file_record.get("normalization_result")
    tables = []
    if isinstance(normalization, dict) and isinstance(normalization.get("tables"), list):
        for descriptor in normalization["tables"][:20]:
            if not isinstance(descriptor, dict):
                continue
            tables.append(
                {
                    "table_index": descriptor.get("table_index"),
                    "table_name": descriptor.get("table_name"),
                    "row_count": descriptor.get("row_count"),
                    "column_count": descriptor.get("column_count"),
                    "schema": descriptor.get("schema"),
                }
            )
    return {
        "id": file_record.get("id"),
        "dataset_id": file_record.get("dataset_id"),
        "filename": file_record.get("filename"),
        "file_size_bytes": file_record.get("file_size_bytes"),
        "detected_format": file_record.get("detected_format"),
        "status": file_record.get("status"),
        "normalization_status": file_record.get("normalization_status"),
        "tables": tables,
    }


def _profile_summary(response: dict[str, Any]) -> dict[str, Any]:
    profile = response.get("profile_result")
    if not isinstance(profile, dict):
        return {"dataset_id": response.get("dataset_id"), "file_id": response.get("file_id")}
    tables = []
    for table in profile.get("tables", [])[:20] if isinstance(profile.get("tables"), list) else []:
        if not isinstance(table, dict):
            continue
        columns = []
        for column in table.get("columns", [])[:200] if isinstance(table.get("columns"), list) else []:
            if not isinstance(column, dict):
                continue
            columns.append({
                key: column[key]
                for key in (
                    "name",
                    "position",
                    "data_type",
                    "row_count",
                    "null_count",
                    "null_percentage",
                    "distinct_count",
                    "distinct_count_exact",
                    "unique_non_null_ratio",
                )
                if key in column and isinstance(column[key], (str, int, float, bool, type(None)))
            })
        tables.append({
            "name": table.get("name"),
            "row_count": table.get("row_count"),
            "column_count": table.get("column_count"),
            "columns": columns,
        })
    return {
        "dataset_id": response.get("dataset_id"),
        "file_id": response.get("file_id"),
        "profile": {
            "profile_version": profile.get("profile_version"),
            "tables": tables,
        },
    }


def _quality_summary(response: dict[str, Any]) -> dict[str, Any]:
    report = response.get("quality_report")
    if not isinstance(report, dict):
        return {
            key: response.get(key)
            for key in ("dataset_id", "file_id", "status", "report_is_current", "error")
        }
    findings = []
    for finding in report.get("findings", [])[:500] if isinstance(report.get("findings"), list) else []:
        if not isinstance(finding, dict):
            continue
        findings.append({
            key: finding[key]
            for key in ("rule_id", "title", "severity", "classification", "scope", "message")
            if key in finding
        })
    return {
        "dataset_id": response.get("dataset_id"),
        "file_id": response.get("file_id"),
        "status": response.get("status"),
        "report_is_current": response.get("report_is_current"),
        "summary": report.get("summary"),
        "findings": findings,
        "warnings": report.get("warnings", []),
    }


def _http_detail(error: HTTPException) -> tuple[str, str]:
    detail = error.detail
    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")
        if isinstance(code, str) and isinstance(message, str):
            return code, message[:1000]
    if isinstance(detail, str):
        return f"http_{error.status_code}", detail[:1000]
    return f"http_{error.status_code}", "The requested data operation could not be completed."


def _call_api_tool(
    call: Callable[..., Any],
    *args: Any,
    name: str,
    **kwargs: Any,
) -> ToolResult:
    try:
        data = call(*args, **kwargs)
    except HTTPException as error:
        code, message = _http_detail(error)
        return ToolResult(
            status="failed",
            summary=message,
            error_code=code,
        )
    except Exception as error:
        logger.error("Agent tool %s failed (%s)", name, type(error).__name__)
        return ToolResult(
            status="failed",
            summary="The requested data operation failed. Retry or choose another dataset.",
            error_code="tool_execution_failed",
        )
    bounded = _bound(data, name)
    return ToolResult(
        status="succeeded",
        summary=f"{name.replace('_', ' ').capitalize()} completed.",
        data=bounded,
    )


def _main():
    from app import main

    return main


def _list_projects(_arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(main.list_projects, name="list_projects")


def _list_datasets(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(
        main.list_project_datasets,
        arguments["project_id"],
        name="list_datasets",
    )


def _list_files(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    try:
        files = main.list_dataset_files(arguments["dataset_id"])
    except HTTPException as error:
        code, message = _http_detail(error)
        return ToolResult(status="failed", summary=message, error_code=code)
    except Exception as error:
        logger.error("Agent tool list_files failed (%s)", type(error).__name__)
        return ToolResult(
            status="failed",
            summary="Dataset files could not be retrieved.",
            error_code="tool_execution_failed",
        )
    data = [_clean_file(item) for item in files]
    return ToolResult(
        status="succeeded",
        summary=f"Found {len(data)} dataset file(s).",
        data=_bound(data, "list_files"),
    )


def _get_profile(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    result = _call_api_tool(
        main.get_dataset_file_profile,
        arguments["dataset_id"],
        arguments["file_id"],
        name="get_profile",
    )
    if result.status == "failed" or not isinstance(result.data, dict):
        return result
    summary = _profile_summary(result.data)
    return ToolResult(status="succeeded", summary="Retrieved the stored dataset profile.", data=_bound(summary, "get_profile"))


def _get_quality(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    result = _call_api_tool(
        main.get_dataset_file_quality_analysis,
        arguments["dataset_id"],
        arguments["file_id"],
        name="get_quality_report",
    )
    if result.status == "failed" or not isinstance(result.data, dict):
        return result
    summary = _quality_summary(result.data)
    warnings = ()
    if summary.get("report_is_current") is False:
        warnings = ("The stored quality report is not current for the dataset inputs.",)
    return ToolResult(
        status="succeeded",
        summary="Retrieved the stored quality report.",
        data=_bound(summary, "get_quality_report"),
        warnings=warnings,
    )


def _get_eda(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(
        main.get_dataset_file_eda_analysis,
        arguments["dataset_id"],
        arguments["file_id"],
        arguments["analysis_id"],
        name="get_eda_result",
    )


def _list_eda(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(
        main.list_dataset_file_eda_analyses,
        arguments["dataset_id"],
        arguments["file_id"],
        limit=arguments["limit"],
        offset=arguments["offset"],
        name="list_eda_results",
    )


def _list_schema(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(
        main.list_dataset_schema_analyses,
        arguments["dataset_id"],
        limit=arguments["limit"],
        offset=arguments["offset"],
        name="list_schema_analyses",
    )


def _get_schema(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    return _call_api_tool(
        main.get_dataset_schema_analysis,
        arguments["dataset_id"],
        arguments["analysis_id"],
        name="get_schema_analysis",
    )


TOOL_DEFINITIONS = (
    ToolDefinition(
        "list_projects",
        "Finding projects",
        "List the projects currently available in this local InsightOS instance.",
        EmptyArguments,
        _list_projects,
    ),
    ToolDefinition(
        "list_datasets",
        "Finding datasets",
        "List datasets within a project.",
        ProjectArguments,
        _list_datasets,
    ),
    ToolDefinition(
        "list_files",
        "Inspecting dataset files",
        "List file names, readiness, and normalized table structure for a dataset. Storage keys and raw file values are never returned.",
        DatasetArguments,
        _list_files,
    ),
    ToolDefinition(
        "get_profile",
        "Reviewing a data profile",
        "Retrieve a stored deterministic profile for a ready dataset file. This does not recompute a profile.",
        FileArguments,
        _get_profile,
    ),
    ToolDefinition(
        "get_quality_report",
        "Reviewing data quality",
        "Retrieve the stored quality report for a dataset file, summarized without sample values.",
        FileArguments,
        _get_quality,
    ),
    ToolDefinition(
        "get_eda_result",
        "Retrieving analysis results",
        "Retrieve a saved exploratory data analysis result.",
        EDAAnalysisArguments,
        _get_eda,
    ),
    ToolDefinition(
        "list_eda_results",
        "Finding previous analyses",
        "List saved exploratory analyses for a dataset file.",
        EDAHistoryArguments,
        _list_eda,
    ),
    ToolDefinition(
        "list_schema_analyses",
        "Finding previous schema analyses",
        "List saved schema analyses for a dataset.",
        SchemaHistoryArguments,
        _list_schema,
    ),
    ToolDefinition(
        "get_schema_analysis",
        "Reviewing schema analysis",
        "Retrieve a saved schema analysis including its structured relationship evidence and stale state.",
        SchemaAnalysisIdArguments,
        _get_schema,
    ),
)


class ToolRegistry:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...] = TOOL_DEFINITIONS,
        *,
        execution_targets: CapabilityRegistry | None = None,
    ):
        self._definitions = {definition.name: definition for definition in definitions}
        self._execution_targets = execution_targets or backend_agent_registry()

    def provider_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            definition.provider_schema()
            for definition in self._definitions.values()
        )

    def execute(self, name: str, raw_arguments: dict[str, Any]) -> tuple[str, ToolResult, str]:
        definition = self._definitions.get(name)
        if definition is None:
            return (
                "Unsupported data operation.",
                ToolResult(
                    status="failed",
                    summary="The requested data operation is not available.",
                    error_code="unknown_tool",
                ),
                "Unavailable operation",
            )
        try:
            arguments = definition.arguments_model.model_validate(raw_arguments)
        except ValidationError:
            return (
                "The requested operation parameters are invalid.",
                ToolResult(
                    status="failed",
                    summary="The requested operation parameters are invalid.",
                    error_code="invalid_arguments",
                ),
                definition.label,
            )
        def execute_tool(
            context: ExecutionContext,
            tool_arguments: dict[str, Any],
        ) -> ToolResult:
            context.report_progress(10)
            try:
                result = definition.execute(tool_arguments)
            except ToolExecutionError as error:
                result = ToolResult(
                    status="failed",
                    summary=error.message,
                    error_code=error.code,
                )
            except Exception as error:
                logger.error("Agent tool %s failed (%s)", name, type(error).__name__)
                result = ToolResult(
                    status="failed",
                    summary="The requested data operation failed.",
                    error_code="tool_execution_failed",
                )
            context.report_progress(90)
            return result

        execution = run_task(
            task=TaskInput(
                task_name=f"agent_tool:{name}",
                payload=arguments.model_dump(),
            ),
            requirements=TaskRequirements(
                required_capabilities=("readonly_agent_tool",),
                data_locality="backend",
                data_classification="internal",
                allowed_targets=("backend",),
                prefer_local=False,
            ),
            registry=self._execution_targets,
            operation=execute_tool,
            result_validator=lambda value: isinstance(value, ToolResult),
            result_serializer=lambda value: {
                "status": value.status,
                "summary": value.summary,
                "data": value.data,
                "warnings": value.warnings,
                "references": value.references,
                "limitations": value.limitations,
                "error_code": value.error_code,
            },
        )
        if execution.status != "completed":
            result = ToolResult(
                status="failed",
                summary=execution.error_message or "The task could not be completed.",
                error_code=execution.error_code or "task_execution_failed",
            )
        else:
            result = replace(
                execution.result,
                references=execution.result.references
                + (
                    {
                        "type": "execution_placement",
                        "target": execution.target_id,
                        "rationale": execution.rationale,
                    },
                ),
            )
        return (
            json.dumps(
                {
                    "status": result.status,
                    "summary": result.summary,
                    "data": result.data,
                    "warnings": list(result.warnings),
                    "references": list(result.references),
                    "limitations": list(result.limitations),
                    "error_code": result.error_code,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
            result,
            definition.label,
        )
