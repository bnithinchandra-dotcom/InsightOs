from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
)

from app.agent.config import MAX_TOOL_OUTPUT_BYTES
from app.agent.contracts import ToolExecutionError, ToolResult
from app.agent.inspection_handoff import build_inspection_handoff
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


class InspectionArguments(FileArguments):
    pass


class EDAExecutionArguments(FileArguments):
    analysis_type: Literal["univariate", "bivariate", "multivariate"]
    table_index: StrictInt = Field(default=0, ge=0)
    selected_columns: list[StrictStr] = Field(min_length=1, max_length=16)


class MonthlyForecastArguments(FileArguments):
    table_index: StrictInt = Field(default=0, ge=0)
    date_column: StrictStr = Field(min_length=1, max_length=255)
    target_column: StrictStr = Field(min_length=1, max_length=255)
    aggregation: Literal["sum"] = "sum"
    horizon: StrictInt = Field(default=3, ge=1, le=24)


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


class MultiDatasetSourceArguments(StrictArguments):
    alias: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    table_index: StrictInt = Field(default=0, ge=0)


class MultiDatasetKeyArguments(StrictArguments):
    alias: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
    column: StrictStr = Field(min_length=1, max_length=255)


class MultiDatasetJoinArguments(StrictArguments):
    left_key: MultiDatasetKeyArguments
    right_key: MultiDatasetKeyArguments
    join_type: Literal["inner", "left"]
    allow_many_to_many: StrictBool = False


class MultiDatasetMetricArguments(StrictArguments):
    name: StrictStr = Field(min_length=1, max_length=64)
    operation: Literal[
        "count",
        "sum",
        "average",
        "minimum",
        "maximum",
        "distinct_count",
    ]
    column: MultiDatasetKeyArguments | None = None


class MultiDatasetRecommendationArguments(StrictArguments):
    sources: list[MultiDatasetSourceArguments] = Field(min_length=2, max_length=4)


class MultiDatasetAnalysisArguments(MultiDatasetRecommendationArguments):
    joins: list[MultiDatasetJoinArguments] = Field(min_length=1, max_length=3)
    dimensions: list[MultiDatasetKeyArguments] = Field(default_factory=list, max_length=16)
    metrics: list[MultiDatasetMetricArguments] = Field(min_length=1, max_length=16)


class ChartSpecificationArguments(StrictArguments):
    title: StrictStr = Field(min_length=1, max_length=120)
    chart_type: Literal["bar", "line", "scatter", "pie"]
    x_field: StrictStr = Field(min_length=1, max_length=255)
    y_field: StrictStr = Field(min_length=1, max_length=64)
    series_field: StrictStr | None = Field(default=None, min_length=1, max_length=255)


class EvidenceReportArguments(MultiDatasetAnalysisArguments):
    charts: list[ChartSpecificationArguments] = Field(default_factory=list, max_length=8)


class EDAEvidenceReportArguments(StrictArguments):
    dataset_id: StrictInt = Field(gt=0)
    file_id: StrictInt = Field(gt=0)
    analysis_id: StrictInt = Field(gt=0)
    chart_columns: list[StrictStr] = Field(default_factory=list, max_length=8)


class DatasetInspectionHandoff(StrictArguments):
    contract_version: Literal[1]
    agent: Literal["data_intelligence"]
    dataset_id: StrictInt
    file_id: StrictInt
    status: Literal["completed"]
    file: dict[str, Any]
    profile: dict[str, Any] | None
    source_fingerprint: StrictStr | None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    label: str
    description: str
    arguments_model: type[StrictArguments]
    execute: Callable[[dict[str, Any]], ToolResult]
    engine_operation: bool = False
    capability: str = "readonly_agent_tool"
    analytics_scope: str | None = None
    analytics_execution: str | None = None
    analytics_outputs: tuple[str, ...] = ()
    workflow_report_result: bool = False

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
    encoded = jsonable_encoder(data)
    try:
        serialized = json.dumps(
            encoded,
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
    return encoded


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
    for table_index, table in enumerate(
        profile.get("tables", [])[:20]
        if isinstance(profile.get("tables"), list)
        else []
    ):
        if not isinstance(table, dict):
            continue
        columns = []
        for column in (
            table.get("columns", [])[:200]
            if isinstance(table.get("columns"), list)
            else []
        ):
            if not isinstance(column, dict):
                continue
            columns.append({
                key: column[key]
                for key in (
                    "name",
                    "position",
                    "physical_types",
                    "missing_value_count",
                    "empty_value_count",
                    "distinct_value_count",
                    "distinct_count_exact",
                )
                if key in column
                and (
                    isinstance(
                        column[key],
                        (str, int, float, bool, type(None)),
                    )
                    or key == "physical_types"
                    and isinstance(column[key], list)
                    and all(isinstance(value, str) for value in column[key])
                )
            })
        tables.append({
            "table_index": table_index,
            "name": table.get("name"),
            "row_count": table.get("row_count"),
            "column_count": table.get("column_count"),
            "duplicate_row_count": table.get("duplicate_row_count"),
            "duplicate_row_count_exact": table.get("duplicate_row_count_exact"),
            "columns": columns,
        })
    return {
        "dataset_id": response.get("dataset_id"),
        "file_id": response.get("file_id"),
        "profile": {
            "version": profile.get("version"),
            "tables": tables,
            "warnings": profile.get("warnings", []),
            "unsupported_analyses": profile.get("unsupported_analyses", []),
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
            for key in (
                "finding_id",
                "rule_id",
                "rule_version",
                "title",
                "severity",
                "classification",
                "scope",
                "evidence",
                "denominator",
                "confidence",
                "confidence_basis",
                "recommended_investigation",
            )
            if key in finding
        })
    return {
        "dataset_id": response.get("dataset_id"),
        "file_id": response.get("file_id"),
        "status": response.get("status"),
        "report_is_current": response.get("report_is_current"),
        "analysis_key": report.get("analysis", {}).get("analysis_key"),
        "completion_status": report.get("completion_status"),
        "summary": report.get("summary"),
        "findings": findings,
        "coverage": report.get("coverage"),
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


def _get_inspection_handoff(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    file_result = _call_api_tool(
        main.get_dataset_file,
        arguments["dataset_id"],
        arguments["file_id"],
        name="get_inspection_file",
    )
    profile_result = _call_api_tool(
        main.get_dataset_file_profile,
        arguments["dataset_id"],
        arguments["file_id"],
        name="get_inspection_profile",
    )
    quality_result = _call_api_tool(
        main.get_dataset_file_quality_analysis,
        arguments["dataset_id"],
        arguments["file_id"],
        name="get_inspection_quality",
    )
    schema_list_result = _call_api_tool(
        main.list_dataset_schema_analyses,
        arguments["dataset_id"],
        limit=100,
        offset=0,
        name="list_inspection_schema_analyses",
    )
    for result in (
        file_result,
        profile_result,
        quality_result,
        schema_list_result,
    ):
        if result.status == "failed":
            return result

    schema_analysis = None
    schema_items = (
        schema_list_result.data.get("items")
        if isinstance(schema_list_result.data, dict)
        else None
    )
    if isinstance(schema_items, list):
        current_analysis = next(
            (
                item
                for item in schema_items
                if isinstance(item, dict)
                and item.get("status") == "Completed"
                and item.get("stale") is False
                and isinstance(item.get("id"), int)
                and not isinstance(item.get("id"), bool)
            ),
            None,
        )
        if current_analysis is not None:
            detail = _call_api_tool(
                main.get_dataset_schema_analysis,
                arguments["dataset_id"],
                current_analysis["id"],
                name="get_inspection_schema_analysis",
            )
            if detail.status == "failed":
                return detail
            schema_analysis = detail.data

    missing_values = 0
    profile_data = profile_result.data
    if isinstance(profile_data, dict):
        profile = profile_data.get("profile_result")
        if isinstance(profile, dict):
            for table in profile.get("tables", []):
                if isinstance(table, dict):
                    for column in table.get("columns", []):
                        if isinstance(column, dict):
                            count = column.get("missing_value_count")
                            if isinstance(count, int) and not isinstance(count, bool):
                                missing_values += count

    operations = []
    if missing_values:
        catalog = _call_api_tool(
            main.list_preprocessing_operations,
            name="list_preprocessing_operations",
        )
        if catalog.status == "failed":
            return catalog
        if isinstance(catalog.data, dict) and isinstance(
            catalog.data.get("operations"), list
        ):
            operations = catalog.data["operations"]

    try:
        handoff = build_inspection_handoff(
            file_record=file_result.data,
            profile_response=profile_result.data,
            quality_response=quality_result.data,
            schema_analysis=schema_analysis,
            preprocessing_operations=operations,
        )
    except (KeyError, TypeError, ValueError) as error:
        logger.warning(
            "Inspection handoff validation failed for file %s (%s)",
            arguments["file_id"],
            type(error).__name__,
        )
        return ToolResult(
            status="failed",
            summary="Stored inspection results could not be validated into a handoff.",
            error_code="inspection_handoff_invalid",
        )
    return ToolResult(
        status="succeeded",
        summary=(
            "Built a schema-validated inspection handoff from stored dataset results."
        ),
        data=_bound(handoff, "get_inspection_handoff"),
        warnings=tuple(handoff["unresolved"]),
        limitations=(
            "The handoff references persisted records; JEV does not resolve these references.",
            "The model's final natural-language response is not claim-validated.",
        ),
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


def _authorized_engine_file(
    main: Any,
    arguments: dict[str, Any],
    *,
    tool_name: str,
) -> ToolResult:
    file_result = _call_api_tool(
        main.get_dataset_file,
        arguments["dataset_id"],
        arguments["file_id"],
        name=tool_name,
    )
    if file_result.status == "failed":
        return file_result
    record = file_result.data
    if not isinstance(record, dict) or record.get("status") != "Ready":
        return ToolResult(
            status="failed",
            summary="This engine requires a ready dataset file.",
            data=record if isinstance(record, dict) else None,
            error_code="file_not_ready",
        )
    clean_file = _clean_file(record)
    profile = _profile_summary(record).get("profile")
    try:
        findings = DatasetInspectionHandoff.model_validate(
            {
                "contract_version": 1,
                "agent": "data_intelligence",
                "dataset_id": arguments["dataset_id"],
                "file_id": arguments["file_id"],
                "status": "completed",
                "file": clean_file,
                "profile": profile,
                "source_fingerprint": record.get("checksum"),
            }
        ).model_dump(mode="json")
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="The selected file inspection did not satisfy its handoff contract.",
            error_code="inspection_handoff_invalid",
        )
    return ToolResult(
        status="succeeded",
        summary="Inspected the selected file metadata and available stored profile before engine execution.",
        data=findings,
        references=(
            {
                "type": "dataset_file_inspection",
                "dataset_id": arguments["dataset_id"],
                "file_id": arguments["file_id"],
                "source_fingerprint": record.get("checksum"),
            },
        ),
    )


def _attach_inspection(
    result: ToolResult,
    inspection: ToolResult,
) -> ToolResult:
    try:
        validated_inspection = DatasetInspectionHandoff.model_validate(
            inspection.data
        ).model_dump(mode="json")
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="Engine execution could not consume a valid data-inspection handoff.",
            error_code="inspection_handoff_invalid",
            references=inspection.references,
        )
    engine_data = result.data if isinstance(result.data, dict) else {}
    return replace(
        result,
        data={
            **engine_data,
            "dataset_id": engine_data.get(
                "dataset_id",
                validated_inspection["dataset_id"],
            ),
            "file_id": engine_data.get(
                "file_id",
                validated_inspection["file_id"],
            ),
            "agent1_inspection": validated_inspection,
        },
        references=result.references + inspection.references,
        warnings=result.warnings + inspection.warnings,
    )


def _run_normalization(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    inspection = _authorized_engine_file(
        main,
        arguments,
        tool_name="normalize_file",
    )
    if inspection.status != "succeeded":
        return inspection
    result = _call_api_tool(
        main.normalize_dataset_file,
        arguments["dataset_id"],
        arguments["file_id"],
        name="normalize_file",
    )
    if result.status == "failed":
        return _attach_inspection(result, inspection)
    status = result.data.get("status") if isinstance(result.data, dict) else None
    if status != "Ready":
        return _attach_inspection(ToolResult(
            status="failed",
            summary="File normalization did not complete.",
            data=result.data,
            error_code="normalization_incomplete",
        ), inspection)
    return _attach_inspection(ToolResult(
        status="succeeded",
        summary="The existing normalization engine completed and persisted its result.",
        data=result.data,
        warnings=result.warnings,
        references=result.references,
    ), inspection)


def _run_quality_analysis(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    inspection = _authorized_engine_file(
        main,
        arguments,
        tool_name="run_quality_analysis",
    )
    if inspection.status != "succeeded":
        return inspection
    result = _call_api_tool(
        main.analyze_dataset_file_quality,
        arguments["dataset_id"],
        arguments["file_id"],
        name="run_quality_analysis",
    )
    if result.status == "failed":
        return _attach_inspection(result, inspection)
    status = result.data.get("status") if isinstance(result.data, dict) else None
    if status not in {"Completed", "PartiallyCompleted"}:
        return _attach_inspection(ToolResult(
            status="failed",
            summary="Quality analysis did not complete.",
            data=result.data,
            error_code="quality_analysis_incomplete",
        ), inspection)
    warnings = (
        ("Quality analysis completed with partial coverage.",)
        if status == "PartiallyCompleted"
        else ()
    )
    return _attach_inspection(ToolResult(
        status="succeeded",
        summary="The existing deterministic quality engine completed.",
        data=result.data,
        warnings=warnings,
        references=result.references,
    ), inspection)


def _run_eda_analysis(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    inspection = _authorized_engine_file(
        main,
        arguments,
        tool_name="run_eda_analysis",
    )
    if inspection.status != "succeeded":
        return inspection
    request = {
        "schema_version": 1,
        "analysis_type": arguments["analysis_type"],
        "table_index": arguments["table_index"],
        "selected_columns": arguments["selected_columns"],
    }
    result = _call_api_tool(
        main.analyze_dataset_file_eda,
        arguments["dataset_id"],
        arguments["file_id"],
        request=request,
        name="run_eda_analysis",
    )
    if result.status == "failed":
        return _attach_inspection(result, inspection)
    status = result.data.get("status") if isinstance(result.data, dict) else None
    if status not in {"Completed", "PartiallyCompleted"}:
        return _attach_inspection(ToolResult(
            status="failed",
            summary="EDA did not complete.",
            data=result.data,
            error_code="eda_incomplete",
        ), inspection)
    warnings = (
        ("EDA completed with partial results.",)
        if status == "PartiallyCompleted"
        else ()
    )
    return _attach_inspection(ToolResult(
        status="succeeded",
        summary="The existing deterministic EDA engine completed and persisted its result.",
        data=result.data,
        warnings=warnings,
        references=result.references,
    ), inspection)


def _run_monthly_forecast(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    inspection = _authorized_engine_file(
        main,
        arguments,
        tool_name="run_monthly_forecast",
    )
    if inspection.status != "succeeded":
        return inspection
    try:
        request = main.MonthlyForecastRequest.model_validate(arguments)
    except ValidationError:
        return _attach_inspection(
            ToolResult(
                status="failed",
                summary="The monthly forecast parameters are invalid.",
                error_code="invalid_arguments",
            ),
            inspection,
        )
    created = _call_api_tool(
        main.create_monthly_forecast,
        request,
        name="run_monthly_forecast",
    )
    if created.status != "succeeded" or not isinstance(created.data, dict):
        return _attach_inspection(created, inspection)
    workflow_id = created.data.get("workflow_id")
    if not isinstance(workflow_id, str):
        return _attach_inspection(
            ToolResult(
                status="failed",
                summary="The forecast workflow identifier was not persisted.",
                error_code="forecast_workflow_missing",
            ),
            inspection,
        )
    persisted = _call_api_tool(
        main.get_monthly_forecast,
        workflow_id,
        name="run_monthly_forecast",
    )
    if persisted.status != "succeeded" or not isinstance(persisted.data, dict):
        return _attach_inspection(persisted, inspection)
    task = persisted.data.get("task")
    result = task.get("result") if isinstance(task, dict) else None
    if (
        not isinstance(task, dict)
        or task.get("status") != "completed"
        or not isinstance(result, dict)
    ):
        return _attach_inspection(
            ToolResult(
                status="failed",
                summary="The persisted monthly forecast did not complete successfully.",
                data={
                    "workflow_id": workflow_id,
                    "task": task,
                },
                error_code=(
                    task.get("error_code")
                    if isinstance(task.get("error_code"), str)
                    else "forecast_task_incomplete"
                ),
            ),
            inspection,
        )
    return _attach_inspection(
        ToolResult(
            status="succeeded",
            summary="The deterministic monthly forecast completed and its persisted result was verified.",
            data={
                "workflow_id": workflow_id,
                "status": task["status"],
                "created_at": persisted.data.get("created_at"),
                "completed_at": persisted.data.get("completed_at"),
                **result,
            },
            warnings=tuple(
                warning
                for warning in result.get("warnings", [])
                if isinstance(warning, str)
            ),
            references=tuple(
                reference
                for reference in result.get("references", [])
                if isinstance(reference, dict)
            ),
            limitations=tuple(
                limitation
                for limitation in result.get("limitations", [])
                if isinstance(limitation, str)
            ),
        ),
        inspection,
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


def _recommend_multi_dataset_joins(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    try:
        request = main.MultiDatasetRecommendationRequest.model_validate(arguments)
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="The selected dataset sources are invalid.",
            error_code="invalid_arguments",
        )
    result = _call_api_tool(
        main.recommend_multi_dataset_relationships,
        request,
        name="recommend_multi_dataset_joins",
    )
    return _attach_multi_dataset_references(result)


def _analyze_multiple_datasets(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    try:
        request = main.MultiDatasetAnalysisRequest.model_validate(arguments)
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="The multi-dataset join or aggregation request is invalid.",
            error_code="invalid_arguments",
        )
    result = _call_api_tool(
        main.analyze_multiple_datasets,
        request,
        name="analyze_multiple_datasets",
    )
    return _attach_multi_dataset_references(result)


def _generate_evidence_report(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    try:
        request = main.MultiDatasetReportRequest.model_validate(arguments)
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="The analytics report request is invalid.",
            error_code="invalid_arguments",
        )
    return _attach_multi_dataset_references(
        _call_api_tool(
            main.generate_analytics_report,
            request,
            name="generate_evidence_report",
        )
    )


def _generate_eda_evidence_report(arguments: dict[str, Any]) -> ToolResult:
    main = _main()
    try:
        request = main.EDAReportRequest.model_validate(arguments)
    except ValidationError:
        return ToolResult(
            status="failed",
            summary="The stored EDA report request is invalid.",
            error_code="invalid_arguments",
        )
    return _call_api_tool(
        main.generate_eda_evidence_report,
        request,
        name="generate_eda_evidence_report",
    )


def _attach_multi_dataset_references(result: ToolResult) -> ToolResult:
    if result.status != "succeeded" or not isinstance(result.data, dict):
        return result
    sources = result.data.get("input_sources", result.data.get("sources", []))
    references = [
        {
            "type": "multi_dataset_source",
            "alias": source.get("alias"),
            "dataset_id": source.get("dataset_id"),
            "file_id": source.get("file_id"),
            "table_index": source.get("table_index"),
            "sha256": source.get("source_sha256"),
        }
        for source in sources
        if isinstance(source, dict)
    ] if isinstance(sources, list) else []
    fingerprint = result.data.get("execution_fingerprint")
    if isinstance(fingerprint, str):
        references.append(
            {
                "type": "multi_dataset_execution",
                "sha256": fingerprint,
            }
        )
    return replace(result, references=tuple(references))


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
        "recommend_multi_dataset_joins",
        "Recommending dataset relationships",
        "Compare explicitly selected ready dataset tables and return explainable join-key candidates using names, types, uniqueness, null rates, and observed value overlap. Recommendations are hypotheses, not approved joins.",
        MultiDatasetRecommendationArguments,
        _recommend_multi_dataset_joins,
        analytics_scope="multiple_datasets",
        analytics_execution="deterministic",
        analytics_outputs=("join_recommendations", "source_evidence"),
    ),
    ToolDefinition(
        "analyze_multiple_datasets",
        "Joining and aggregating datasets",
        "Execute explicitly supplied inner/left join keys across 2 to 4 authorized dataset tables, then calculate deterministic grouped metrics. Use only user-approved exact keys; many-to-many joins require explicit approval and remain row-limited.",
        MultiDatasetAnalysisArguments,
        _analyze_multiple_datasets,
        analytics_scope="multiple_datasets",
        analytics_execution="deterministic",
        analytics_outputs=("grouped_metrics", "source_evidence"),
    ),
    ToolDefinition(
        "generate_evidence_report",
        "Generating an evidence-backed report",
        "Calculate grouped metrics with the deterministic analytics engine, then return measured findings, validated chart specifications, source references, warnings, and provenance. All keys and metrics must be explicitly supplied; the model cannot provide chart values.",
        EvidenceReportArguments,
        _generate_evidence_report,
        analytics_scope="multiple_datasets",
        analytics_execution="deterministic",
        analytics_outputs=("summary", "findings", "charts", "provenance"),
        workflow_report_result=True,
    ),
    ToolDefinition(
        "generate_eda_evidence_report",
        "Reporting stored EDA findings",
        "Turn a completed, current stored EDA result into factual metric findings and deterministic charts with source provenance. This reports existing results and does not rerun analysis.",
        EDAEvidenceReportArguments,
        _generate_eda_evidence_report,
        analytics_scope="one_dataset",
        analytics_execution="deterministic",
        analytics_outputs=("summary", "findings", "charts", "provenance"),
        workflow_report_result=True,
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
        "get_inspection_handoff",
        "Preparing an inspection handoff",
        "Build a versioned, schema-validated handoff from stored profile, quality, and current schema-analysis results. This does not execute preprocessing.",
        InspectionArguments,
        _get_inspection_handoff,
    ),
    ToolDefinition(
        "get_eda_result",
        "Retrieving analysis results",
        "Retrieve a saved exploratory data analysis result.",
        EDAAnalysisArguments,
        _get_eda,
    ),
    ToolDefinition(
        "normalize_file",
        "Normalizing dataset file",
        "Run the existing bounded ETL normalization for an explicitly requested ready file. Writes the verified normalized artifact and metadata; does not execute preprocessing transformations.",
        FileArguments,
        _run_normalization,
        engine_operation=True,
        capability="registered_dataset_engine",
    ),
    ToolDefinition(
        "run_quality_analysis",
        "Running data-quality analysis",
        "Run the existing deterministic quality-analysis engine for an explicitly requested normalized file. Persists its quality report.",
        FileArguments,
        _run_quality_analysis,
        engine_operation=True,
        capability="registered_dataset_engine",
    ),
    ToolDefinition(
        "run_eda_analysis",
        "Running exploratory data analysis",
        "Run the existing deterministic univariate, bivariate, or multivariate EDA engine on explicitly selected columns of a normalized file.",
        EDAExecutionArguments,
        _run_eda_analysis,
        engine_operation=True,
        capability="registered_dataset_engine",
        analytics_scope="one_dataset",
        analytics_execution="deterministic",
        analytics_outputs=("eda_result_reference", "provenance"),
    ),
    ToolDefinition(
        "run_monthly_forecast",
        "Forecasting a monthly series",
        "Run the deterministic monthly trend and seasonality forecast on an explicitly selected authorized normalized file. Select the time column, numeric target, sum aggregation, and a 1–24 month horizon. Returns a persisted workflow result with measured holdout metrics, historical observations, forecast values, provenance, warnings, and chart specification.",
        MonthlyForecastArguments,
        _run_monthly_forecast,
        engine_operation=True,
        capability="registered_dataset_engine",
        analytics_scope="one_dataset",
        analytics_execution="forecast",
        analytics_outputs=("forecast", "evaluation", "warnings", "provenance"),
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


_ENGINE_INTENT_PATTERNS = {
    "normalize_file": re.compile(
        r"\b(?:normalize|normalise)\b|"
        r"\b(?:run|perform|execute)\b.{0,40}\bnormalization\b",
        re.IGNORECASE,
    ),
    "run_quality_analysis": re.compile(
        r"\b(?:run|perform|execute|check|start)\b.{0,40}\bquality\b|"
        r"\bquality\b.{0,40}\b(?:run|perform|execute|check)\b",
        re.IGNORECASE,
    ),
    "run_eda_analysis": re.compile(
        r"\b(?:run|perform|execute|calculate|compute|analy[sz]e)\b.{0,50}"
        r"\b(?:eda|exploratory(?:\s+data\s+analysis)?|distribution|statistics|"
        r"histogram|correlation)\b",
        re.IGNORECASE,
    ),
    "run_monthly_forecast": re.compile(
        r"\b(?:run|create|generate|make|calculate|build)\b.{0,60}"
        r"\b(?:monthly\s+)?forecast\b|"
        r"\b(?:forecast|predict)\b.{0,60}\b(?:sales|revenue|demand|values|"
        r"target|series|monthly|future|months?)\b",
        re.IGNORECASE,
    ),
}

_MULTI_DATASET_INTENT = re.compile(
    r"\b(?:join|joins|joining|merge|merges|merging|combine|combines|combining|"
    r"relationship|relationships|relate|link)\b",
    re.IGNORECASE,
)

_REPORT_INTENT = re.compile(
    r"\b(?:dashboard|chart|visuali[sz]|report|insight|findings?)\b",
    re.IGNORECASE,
)


def _multi_dataset_intent_allowed(context: dict[str, Any] | None) -> bool:
    message = context.get("user_message") if isinstance(context, dict) else None
    return isinstance(message, str) and bool(
        _MULTI_DATASET_INTENT.search(message) or _REPORT_INTENT.search(message)
    )


def _engine_intent_allowed(tool_name: str, context: dict[str, Any] | None) -> bool:
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("dataset_id"), int)
        or isinstance(context.get("dataset_id"), bool)
    ):
        return False
    message = context.get("user_message")
    pattern = _ENGINE_INTENT_PATTERNS.get(tool_name)
    return isinstance(message, str) and pattern is not None and bool(pattern.search(message))


class ToolRegistry:
    def __init__(
        self,
        definitions: tuple[ToolDefinition, ...] = TOOL_DEFINITIONS,
        *,
        execution_targets: CapabilityRegistry | None = None,
    ):
        self._definitions = {definition.name: definition for definition in definitions}
        self._execution_targets = execution_targets or backend_agent_registry()

    def provider_schemas(
        self,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            definition.provider_schema()
            for definition in self._definitions.values()
            if (
                definition.name
                not in {
                    "recommend_multi_dataset_joins",
                    "analyze_multiple_datasets",
                    "generate_evidence_report",
                    "generate_eda_evidence_report",
                }
                or _multi_dataset_intent_allowed(context)
            )
            and (
                not definition.engine_operation
                or _engine_intent_allowed(definition.name, context)
            )
        )

    def is_engine_operation(self, name: str) -> bool:
        definition = self._definitions.get(name)
        return definition is not None and definition.engine_operation

    def analytics_capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "operation": definition.name,
                "description": definition.description,
                "parameter_schema": definition.arguments_model.model_json_schema(),
                "source_scope": definition.analytics_scope,
                "execution_kind": definition.analytics_execution,
                "supported_outputs": list(definition.analytics_outputs),
                "workflow_report_result": definition.workflow_report_result,
                "plan_eligible": definition.workflow_report_result,
                "dependency_ordering": definition.workflow_report_result,
                "supported_result_references": [],
            }
            for definition in self._definitions.values()
            if definition.analytics_scope is not None
        ]

    def validate_workflow_report_parameters(
        self,
        name: str,
        raw_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        definition = self._definitions.get(name)
        if definition is None or not definition.workflow_report_result:
            raise LookupError("This operation does not produce a workflow report result.")
        return definition.arguments_model.model_validate(raw_arguments).model_dump(
            mode="json"
        )

    def prepare_engine_inspection(
        self,
        name: str,
        raw_arguments: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        definition = self._definitions.get(name)
        if definition is None or not definition.engine_operation:
            return ToolResult(
                status="failed",
                summary="The requested inspection is not an engine operation.",
                error_code="unknown_tool",
            )
        if not _engine_intent_allowed(name, context):
            return ToolResult(
                status="failed",
                summary="This engine operation requires an explicit user request and a selected dataset.",
                error_code="engine_execution_not_authorized",
            )
        try:
            arguments = definition.arguments_model.model_validate(raw_arguments)
        except ValidationError:
            return ToolResult(
                status="failed",
                summary="The requested operation parameters are invalid.",
                error_code="invalid_arguments",
            )
        if (
            isinstance(context, dict)
            and isinstance(context.get("dataset_id"), int)
            and not isinstance(context.get("dataset_id"), bool)
            and arguments.dataset_id != context["dataset_id"]
        ):
            return ToolResult(
                status="failed",
                summary="The requested operation is outside the conversation dataset scope.",
                error_code="dataset_scope_mismatch",
            )
        return _authorized_engine_file(
            _main(),
            arguments.model_dump(),
            tool_name=name,
        )

    def execute(
        self,
        name: str,
        raw_arguments: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, ToolResult, str]:
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
        if name in {
            "recommend_multi_dataset_joins",
            "analyze_multiple_datasets",
            "generate_evidence_report",
            "generate_eda_evidence_report",
        } and not _multi_dataset_intent_allowed(context):
            return (
                "Analytics tools require an explicit report, visualization, join, or relationship request.",
                ToolResult(
                    status="failed",
                    summary="Analytics tools require an explicit report, visualization, join, or relationship request.",
                    error_code="multi_dataset_intent_required",
                ),
                definition.label,
            )
        if definition.engine_operation:
            if not _engine_intent_allowed(name, context):
                return (
                    "This engine operation requires an explicit user request and a selected dataset.",
                    ToolResult(
                        status="failed",
                        summary="This engine operation requires an explicit user request and a selected dataset.",
                        error_code="engine_execution_not_authorized",
                    ),
                    definition.label,
                )
            if (
                isinstance(raw_arguments, dict)
                and raw_arguments.get("file_id") is not None
                and not isinstance(raw_arguments.get("file_id"), int)
            ):
                return (
                    "The dataset file identifier is invalid.",
                    ToolResult(
                        status="failed",
                        summary="The dataset file identifier is invalid.",
                        error_code="invalid_arguments",
                    ),
                    definition.label,
                )
            if context.get("_engine_operation_executed"):
                return (
                    "Only one engine operation may run per chat request.",
                    ToolResult(
                        status="failed",
                        summary="Only one engine operation may run per chat request.",
                        error_code="engine_operation_limit",
                    ),
                    definition.label,
                )
        if isinstance(raw_arguments, dict) and isinstance(context, dict):
            requested_dataset_id = raw_arguments.get("dataset_id")
            pinned_dataset_id = context.get("dataset_id")
            if name in {
                "recommend_multi_dataset_joins",
                "analyze_multiple_datasets",
                "generate_evidence_report",
                "generate_eda_evidence_report",
            } and isinstance(pinned_dataset_id, int) and not isinstance(
                pinned_dataset_id, bool
            ):
                source_values = raw_arguments.get("sources")
                selected_source_ids = {
                    source.get("dataset_id")
                    for source in (
                        source_values if isinstance(source_values, list) else []
                    )
                    if isinstance(source, dict)
                    and isinstance(source.get("dataset_id"), int)
                    and not isinstance(source.get("dataset_id"), bool)
                }
                if pinned_dataset_id not in selected_source_ids:
                    return (
                        "The selected dataset must be included in the multi-dataset request.",
                        ToolResult(
                            status="failed",
                            summary="The requested operation is outside the conversation dataset scope.",
                            error_code="dataset_scope_mismatch",
                        ),
                        definition.label,
                    )
            if (
                isinstance(pinned_dataset_id, int)
                and not isinstance(pinned_dataset_id, bool)
                and isinstance(requested_dataset_id, int)
                and not isinstance(requested_dataset_id, bool)
                and requested_dataset_id != pinned_dataset_id
            ):
                return (
                    "The requested operation is outside the conversation dataset scope.",
                    ToolResult(
                        status="failed",
                        summary="The requested operation is outside the conversation dataset scope.",
                        error_code="dataset_scope_mismatch",
                    ),
                    definition.label,
                )
            requested_project_id = raw_arguments.get("project_id")
            pinned_project_id = context.get("project_id")
            if (
                isinstance(pinned_project_id, int)
                and not isinstance(pinned_project_id, bool)
                and isinstance(requested_project_id, int)
                and not isinstance(requested_project_id, bool)
                and requested_project_id != pinned_project_id
            ):
                return (
                    "The requested operation is outside the conversation project scope.",
                    ToolResult(
                        status="failed",
                        summary="The requested operation is outside the conversation project scope.",
                        error_code="project_scope_mismatch",
                    ),
                    definition.label,
                )
        request_context = context
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
            task_context: ExecutionContext,
            tool_arguments: dict[str, Any],
        ) -> ToolResult:
            task_context.report_progress(10)
            try:
                if definition.engine_operation:
                    if isinstance(request_context, dict):
                        request_context["_engine_operation_executed"] = True
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
            task_context.report_progress(90)
            return result

        execution = run_task(
            task=TaskInput(
                task_name=f"agent_tool:{name}",
                payload=arguments.model_dump(),
            ),
            requirements=TaskRequirements(
                required_capabilities=(definition.capability,),
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
