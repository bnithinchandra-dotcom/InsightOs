from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.quality_catalog import (
    QUALITY_RULE_CATALOG_VERSION,
    QUALITY_RULE_CATEGORIES,
    QUALITY_RULE_STATUSES,
    list_quality_rule_catalog,
)


PlanningStatus = Literal[
    "ready",
    "needs_context",
    "partial",
    "planned",
    "unsupported",
]
ExecutionStatus = Literal[
    "Completed",
    "Skipped",
    "NotApplicable",
    "BudgetExhausted",
    "Failed",
]


@dataclass(frozen=True)
class QualityPlanningContext:
    dataset_id: int
    file_id: int
    dataset_status: str | None
    file_status: str | None
    detected_format: str | None
    profile_result: dict | None
    normalization_result: dict | None
    quality_report: dict | None
    quality_report_current: bool


class QualityPlanningContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: int
    file_id: int
    dataset_status: str | None
    file_status: str | None
    detected_format: str | None
    profile_available: bool
    normalized_metadata_available: bool
    current_quality_report_available: bool


class QualityRuleRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    status: Literal["implemented", "partial", "planned", "unsupported"]
    planning_status: PlanningStatus
    rationale: str
    prerequisites: list[str]
    missing_context: list[str]
    execution_supported: bool
    requires_user_decision: bool
    required_decisions: list[str]
    previous_execution_status: ExecutionStatus | None


class QualityRulePlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    context: QualityPlanningContextResponse
    recommendations: list[QualityRuleRecommendation]


_PREREQUISITE_MISSING_CONTEXT = {
    "validated_normalization_metadata": "validated normalized metadata",
    "per_column_null_and_empty_counts": "per-column null and empty counts",
    "validated_source_type_profile": "validated source type profile",
    "normalized_column_schema": "normalized column schema",
    "validated_profile_result": "validated column profile",
    "exact_profile_column_cardinality": "exact per-column distinct counts",
    "verified_normalized_parquet": "validated normalized Parquet outputs",
    "normalized_numeric_columns": "normalized numeric columns",
    "normalized_string_columns": "normalized string columns",
}


def _available_prerequisites(
    context: QualityPlanningContext,
) -> set[str]:
    available = set()
    profile = context.profile_result
    normalized = context.normalization_result

    if isinstance(profile, dict) and isinstance(profile.get("tables"), list):
        available.add("validated_source_type_profile")
        profile_tables = profile["tables"]
    if isinstance(normalized, dict) and isinstance(normalized.get("tables"), list):
        tables = normalized["tables"]
        available.add("validated_normalization_metadata")
        available.add("normalized_column_schema")
        available.add("per_column_null_and_empty_counts")
        if tables and all(
            table.get("object_key") is not None
            or (
                table.get("row_count") == 0
                and table.get("column_count") == 0
            )
            for table in tables
        ):
            available.add("verified_normalized_parquet")
        if any(
            field.get("type", "").lower().startswith(
                ("int", "uint", "float", "double", "decimal")
            )
            for table in tables
            for field in table.get("schema", [])
        ):
            available.add("normalized_numeric_columns")
        if any(
            field.get("type", "").lower().startswith(
                ("string", "large_string")
            )
            for table in tables
            for field in table.get("schema", [])
        ):
            available.add("normalized_string_columns")
        profile_tables = (
            profile.get("tables") if isinstance(profile, dict) else None
        )
        if (
            isinstance(profile_tables, list)
            and len(profile_tables) == len(tables)
            and all(
                isinstance(profile_table, dict)
                and isinstance(table, dict)
                and profile_table.get("name") == table.get("table_name")
                and profile_table.get("row_count") == table.get("row_count")
                and isinstance(table.get("row_count"), int)
                and not isinstance(table.get("row_count"), bool)
                and table["row_count"] >= 0
                and isinstance(profile_table.get("columns"), list)
                and len(profile_table["columns"]) == len(table.get("schema", []))
                and all(
                    isinstance(column, dict)
                    and isinstance(field, dict)
                    and column.get("name") == field.get("name")
                    and column.get("position") == position
                    and column.get("distinct_count_exact") is True
                    and isinstance(column.get("distinct_value_count"), int)
                    and not isinstance(column.get("distinct_value_count"), bool)
                    and column["distinct_value_count"] >= 0
                    and isinstance(column.get("missing_value_count"), int)
                    and not isinstance(column.get("missing_value_count"), bool)
                    and column["missing_value_count"] >= 0
                    and isinstance(column.get("empty_value_count"), int)
                    and not isinstance(column.get("empty_value_count"), bool)
                    and column["empty_value_count"] >= 0
                    and column["missing_value_count"]
                    + column["empty_value_count"]
                    <= table["row_count"]
                    and column["distinct_value_count"]
                    >= int(
                        table["row_count"] - column["missing_value_count"] > 0
                    )
                    and column["distinct_value_count"]
                    <= table["row_count"] - column["missing_value_count"]
                    and not (
                        column["empty_value_count"] > 0
                        and column["distinct_value_count"] == 0
                    )
                    for position, (column, field) in enumerate(
                        zip(profile_table["columns"], table["schema"])
                    )
                )
                for profile_table, table in zip(profile_tables, tables)
            )
        ):
            available.add("validated_profile_result")
            available.add("exact_profile_column_cardinality")
    return available


def _previous_execution_status(
    quality_report: dict | None,
    rule_id: str,
) -> ExecutionStatus | None:
    if not isinstance(quality_report, dict):
        return None
    coverage = quality_report.get("coverage")
    checks = coverage.get("checks") if isinstance(coverage, dict) else None
    if not isinstance(checks, list):
        return None
    check_id = f"quality_rules.rule.{rule_id}"
    for check in checks:
        if not isinstance(check, dict) or check.get("check_id") != check_id:
            continue
        status = check.get("status")
        if status in {
            "Completed",
            "Skipped",
            "NotApplicable",
            "BudgetExhausted",
            "Failed",
        }:
            return status
    return None


def plan_quality_rules(
    context: QualityPlanningContext,
    *,
    category: str | None = None,
    status: str | None = None,
) -> QualityRulePlanResponse:
    if category is not None and category not in QUALITY_RULE_CATEGORIES:
        raise ValueError("Unknown quality-rule category.")
    if status is not None and status not in QUALITY_RULE_STATUSES:
        raise ValueError("Unknown quality-rule implementation status.")

    available = _available_prerequisites(context)
    recommendations = []
    for entry in list_quality_rule_catalog(category=category, status=status):
        missing_context = []
        for prerequisite in entry["prerequisites"]:
            if prerequisite not in available:
                label = _PREREQUISITE_MISSING_CONTEXT.get(
                    prerequisite,
                    f"required context: {prerequisite}",
                )
                if label not in missing_context:
                    missing_context.append(label)
        required_decisions = list(entry["required_approvals"])
        for decision in required_decisions:
            label = f"user decision: {decision}"
            if label not in missing_context:
                missing_context.append(label)

        rule_status = entry["status"]
        execution_supported = (
            rule_status == "implemented" and not missing_context
        )
        if rule_status == "partial":
            planning_status = "partial"
            rationale = (
                "This catalog entry is partial; no executable rule is available "
                "and it cannot be dispatched."
            )
        elif rule_status == "planned":
            planning_status = "planned"
            rationale = (
                "This catalog entry is planned; it is discoverable but cannot "
                "be dispatched."
            )
        elif rule_status == "unsupported":
            planning_status = "unsupported"
            rationale = (
                "This catalog entry is explicitly unsupported and cannot be dispatched."
            )
        elif execution_supported:
            planning_status = "ready"
            rationale = (
                "The rule has an implemented executor and its required context "
                "is available. This plan does not execute it."
            )
        else:
            planning_status = "needs_context"
            rationale = (
                "The rule is implemented, but required context or user decisions "
                "are not available."
            )

        previous_status = (
            _previous_execution_status(context.quality_report, entry["rule_id"])
            if context.quality_report_current
            else None
        )
        if previous_status is not None:
            rationale += (
                f" The current quality report records a prior status of "
                f"{previous_status}."
            )

        recommendations.append(
            QualityRuleRecommendation(
                rule_id=entry["rule_id"],
                status=rule_status,
                planning_status=planning_status,
                rationale=rationale,
                prerequisites=list(entry["prerequisites"]),
                missing_context=missing_context,
                execution_supported=execution_supported,
                requires_user_decision=bool(required_decisions),
                required_decisions=required_decisions,
                previous_execution_status=previous_status,
            )
        )

    return QualityRulePlanResponse(
        catalog_version=QUALITY_RULE_CATALOG_VERSION,
        context=QualityPlanningContextResponse(
            dataset_id=context.dataset_id,
            file_id=context.file_id,
            dataset_status=context.dataset_status,
            file_status=context.file_status,
            detected_format=context.detected_format,
            profile_available=context.profile_result is not None,
            normalized_metadata_available=context.normalization_result is not None,
            current_quality_report_available=(
                context.quality_report_current
                and context.quality_report is not None
            ),
        ),
        recommendations=recommendations,
    )
