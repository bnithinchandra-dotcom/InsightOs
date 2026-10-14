import copy
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Callable, Iterable

import pyarrow.parquet as pq

from app.execution import ExecutionBudget, ExecutionBudgetExceeded
from app.quality_catalog import (
    get_quality_rule_catalog_entry,
    quality_rule_catalog_entries,
    validate_quality_rule_configuration,
)
from app.profiling import _normalize, _row_fingerprint


logger = logging.getLogger(__name__)

MAX_RULES = 255
MAX_RULE_FINDINGS = 500
MAX_RULE_EXAMPLES = 5
MAX_RULE_RESULT_BYTES = 1024 * 1024
MAX_RULE_INPUT_BYTES = 600 * 1024 * 1024
MAX_RULE_MEMORY_BYTES = 4 * 1024 * 1024
MAX_BUSINESS_KEY_VALUE_BYTES = 64 * 1024
MAX_EXACT_ROW_VALUE_BYTES = 128 * 1024
MAX_OUTLIER_VALUE_BYTES = 128 * 1024
MAX_SOURCE_PHYSICAL_TYPES = 32
QUALITY_RULESET_VERSION = "4"
_RULE_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_RULE_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}$")
_CLASSIFICATIONS = {
    "confirmed_violation",
    "suspected_issue",
    "unusual_candidate",
}
_SEVERITIES = {"info", "low", "medium", "high", "critical"}
_TEXT_LIMIT = 512
_CONFIGURATION_BYTES_LIMIT = 64 * 1024


class QualityRuleConfigurationError(ValueError):
    pass


class QualityRuleInputError(ValueError):
    pass


class RuleBudgetExhausted(Exception):
    pass


@dataclass(frozen=True)
class RuleFinding:
    classification: str
    title: str
    description: str
    scope: dict
    affected_count: int
    denominator_basis: str
    denominator_count: int | None
    denominator_reason: str | None
    evidence: dict
    confidence: float
    confidence_basis: str
    recommended_investigation: str


@dataclass(frozen=True)
class QualityRule:
    rule_id: str
    version: str
    scope: str
    severity: str
    prerequisites: tuple[str, ...]
    configuration_defaults: dict
    validate_configuration: Callable[[dict], dict]
    unsupported_reason: Callable[[dict, dict], str | None]
    evaluate: Callable[[dict, dict, "RuleBudget"], Iterable[RuleFinding]]


@dataclass(frozen=True)
class RuleExecutionResult:
    rule_id: str
    rule_version: str
    status: str
    severity: str
    findings: tuple[RuleFinding, ...]
    affected_count: int
    denominator_count: int | None
    rows_considered: int
    budget_limit: int
    budget_used: int
    reason: str | None


class RuleBudget:
    def __init__(self, limit: int):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Quality rule comparison limits must be positive integers.")
        self._budget = ExecutionBudget(
            {
                "comparisons": limit,
                "tracking_memory_bytes": MAX_RULE_MEMORY_BYTES,
            }
        )
        self._limit = limit

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._budget.used("comparisons")

    @property
    def memory_used(self) -> int:
        return self._budget.used("tracking_memory_bytes")

    def consume_comparisons(self, count: int = 1) -> None:
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
        ):
            raise RuleBudgetExhausted
        try:
            self._budget.consume("comparisons", count)
        except ExecutionBudgetExceeded:
            raise RuleBudgetExhausted from None

    def consume_memory(self, count: int) -> None:
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise RuleBudgetExhausted
        try:
            self._budget.consume("tracking_memory_bytes", count)
        except ExecutionBudgetExceeded:
            raise RuleBudgetExhausted from None


def canonical_json(value) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_missing_value_configuration(configuration: dict) -> dict:
    if not isinstance(configuration, dict) or set(configuration) != {
        "include_empty_strings",
        "include_null_values",
    }:
        raise QualityRuleConfigurationError(
            "Missing-value rule configuration has unsupported fields."
        )
    if any(not isinstance(value, bool) for value in configuration.values()):
        raise QualityRuleConfigurationError(
            "Missing-value rule options must be boolean."
        )
    return dict(configuration)


def _missing_value_unsupported_reason(
    normalization_result: dict,
    _configuration: dict,
) -> str | None:
    tables = normalization_result.get("tables")
    if not isinstance(tables, list):
        return "Validated table metadata is unavailable."
    for table in tables:
        if (
            not isinstance(table, dict)
            or not isinstance(table.get("schema"), list)
            or not isinstance(table.get("row_count"), int)
            or isinstance(table.get("row_count"), bool)
            or table["row_count"] < 0
            or not isinstance(table.get("null_value_counts"), list)
            or not isinstance(table.get("empty_value_counts"), list)
            or len(table["schema"]) != len(table["null_value_counts"])
            or len(table["schema"]) != len(table["empty_value_counts"])
        ):
            return "Validated per-column null and empty-value counts are unavailable."
    return None


def _empty_rule_configuration(configuration: dict) -> dict:
    if not isinstance(configuration, dict) or configuration:
        raise QualityRuleConfigurationError(
            "This rule does not accept configuration options."
        )
    return {}


def _evaluate_missing_value_candidates(
    normalization_result: dict,
    configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for table in normalization_result["tables"]:
        row_count = table["row_count"]
        for position, field in enumerate(table["schema"]):
            budget.consume_comparisons()
            null_count = (
                table["null_value_counts"][position]
                if configuration["include_null_values"]
                else 0
            )
            empty_count = (
                table["empty_value_counts"][position]
                if configuration["include_empty_strings"]
                else 0
            )
            affected_count = null_count + empty_count
            if affected_count > row_count:
                raise ValueError(
                    "Validated value counts exceed the normalized row count."
                )
            if not affected_count:
                continue
            yield RuleFinding(
                classification="unusual_candidate",
                title="Null or empty values present",
                description=(
                    f"{affected_count} of {row_count} rows contain null or "
                    "empty-string values. Their acceptability depends on the "
                    "intended use of this column."
                ),
                scope={
                    "table_index": table["table_index"],
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=affected_count,
                denominator_basis="rows",
                denominator_count=row_count,
                denominator_reason=None,
                evidence={
                    "null_count": null_count,
                    "empty_string_count": empty_count,
                },
                confidence=1.0,
                confidence_basis=(
                    "Counts were validated from the immutable normalized "
                    "Parquet output; this is not a judgment that the values "
                    "are invalid."
                ),
                recommended_investigation=(
                    "Confirm whether null or empty values are permitted for "
                    "this column before defining a business-specific rule."
                ),
            )


MISSING_VALUE_CANDIDATE_RULE = QualityRule(
    rule_id="generic.missing_value_candidates",
    version="1",
    scope="column",
    severity="info",
    prerequisites=(
        "validated_normalization_metadata",
        "per_column_null_and_empty_counts",
    ),
    configuration_defaults={
        "include_empty_strings": True,
        "include_null_values": True,
    },
    validate_configuration=_validate_missing_value_configuration,
    unsupported_reason=_missing_value_unsupported_reason,
    evaluate=_evaluate_missing_value_candidates,
)


def _table_profile(data: dict, table_index: int) -> dict | None:
    profile = data.get("_profile_result")
    tables = profile.get("tables") if isinstance(profile, dict) else None
    if not isinstance(tables, list) or table_index >= len(tables):
        return None
    candidate = tables[table_index]
    if (
        not isinstance(candidate, dict)
        or candidate.get("name") != data["tables"][table_index].get("table_name")
    ):
        return None
    return candidate


def _quality_schema_type(type_name: str) -> str:
    normalized = type_name.lower()
    if normalized.startswith(("int", "uint")):
        return "integer"
    if normalized.startswith(("float", "double", "decimal")):
        return "number"
    if normalized in {"bool", "boolean"}:
        return "boolean"
    if normalized.startswith("string") or normalized.startswith("large_string"):
        return "string"
    if normalized.startswith("date"):
        return "date"
    if normalized.startswith("timestamp"):
        return "datetime"
    if normalized.startswith("time"):
        return "time"
    if normalized in {"null"}:
        return "null"
    return normalized


def _source_type_tables(data: dict) -> list[dict] | None:
    parsing = data.get("_parsing_result")
    if isinstance(parsing, dict):
        detected_format = parsing.get("detected_format")
        metadata = parsing.get("metadata")
        if detected_format == "xlsx" and isinstance(metadata, dict):
            worksheets = metadata.get("worksheets")
            if isinstance(worksheets, list):
                return [
                    {"name": sheet.get("name"), "columns": sheet.get("columns")}
                    for sheet in worksheets
                    if isinstance(sheet, dict)
                ]
        columns = parsing.get("columns")
        if isinstance(columns, list):
            return [{"name": None, "columns": columns}]
    profile = data.get("_profile_result")
    tables = profile.get("tables") if isinstance(profile, dict) else None
    if isinstance(tables, list):
        return tables
    return None


def _type_consistency_unsupported(data: dict, _configuration: dict) -> str | None:
    type_tables = _source_type_tables(data)
    if type_tables is None or len(type_tables) != len(data["tables"]):
        return "Validated source type metadata is unavailable."
    for table_index, table in enumerate(data["tables"]):
        source_table = type_tables[table_index]
        source_columns = source_table.get("columns")
        if not isinstance(source_columns, list):
            return "Validated source type metadata is unavailable."
        if len(source_columns) != len(table.get("schema", [])):
            return "Source type profile does not match the normalized schema."
        if source_table.get("name") != table.get("table_name"):
            return "Source type metadata does not match the normalized table."
        for position, (field, source_column) in enumerate(
            zip(table["schema"], source_columns)
        ):
            if (
                not isinstance(source_column, dict)
                or source_column.get("name") != field.get("name")
                or source_column.get("position", position) != position
                or not isinstance(source_column.get("physical_types"), list)
                or len(source_column["physical_types"]) > MAX_SOURCE_PHYSICAL_TYPES
                or any(not isinstance(item, str) for item in source_column["physical_types"])
                or any(len(item) > 64 for item in source_column["physical_types"])
            ):
                return "Source type metadata does not match the normalized schema."
    return None


def _evaluate_type_consistency(
    data: dict,
    _configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    type_tables = _source_type_tables(data)
    total_columns = sum(len(table["schema"]) for table in data["tables"])
    for table_index, table in enumerate(data["tables"]):
        for position, (field, source_column) in enumerate(
            zip(table["schema"], type_tables[table_index]["columns"])
        ):
            budget.consume_comparisons()
            source_types = source_column.get("physical_types")
            if not isinstance(source_types, list) or not source_types:
                continue
            normalized_type = _quality_schema_type(field["type"])
            observed_types = sorted(
                value for value in set(source_types) if value != "null"
            )
            if not observed_types:
                continue
            normalized_sources = {
                _quality_schema_type(value) for value in observed_types
            }
            mixed = len(normalized_sources) > 1 and not normalized_sources <= {
                "integer",
                "number",
            }
            compatible_types = {normalized_type, "null"}
            if normalized_type == "number":
                compatible_types.add("integer")
            incompatible = not normalized_sources <= compatible_types
            if not mixed and not incompatible:
                continue
            source_type_text = ", ".join(observed_types)
            yield RuleFinding(
                classification="suspected_issue",
                title="Mixed or conflicting source value types",
                description=(
                    "The validated source profile observed multiple or "
                    "incompatible physical types before normalization. "
                    "Review whether the mixed representation is intentional."
                ),
                scope={
                    "table_index": table_index,
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=1,
                denominator_basis="columns",
                denominator_count=total_columns,
                denominator_reason=None,
                evidence={
                    "source_physical_types": source_type_text[:_TEXT_LIMIT],
                    "normalized_type": field["type"],
                    "observed_type_count": len(observed_types),
                },
                confidence=1.0,
                confidence_basis=(
                    "Type labels come from validated parsing/profile metadata; "
                    "the affected row count is not available in that metadata."
                ),
                recommended_investigation=(
                    "Inspect the source column and confirm its intended type "
                    "before defining a stricter type rule."
                ),
            )


TYPE_CONSISTENCY_RULE = QualityRule(
    rule_id="generic.type_consistency",
    version="1",
    scope="column",
    severity="low",
    prerequisites=("validated_source_type_profile", "normalized_column_schema"),
    configuration_defaults={},
    validate_configuration=_empty_rule_configuration,
    unsupported_reason=_type_consistency_unsupported,
    evaluate=_evaluate_type_consistency,
)


def _constant_columns_unsupported(
    data: dict,
    _configuration: dict,
) -> str | None:
    tables = data.get("tables")
    profile = data.get("_profile_result")
    profile_tables = profile.get("tables") if isinstance(profile, dict) else None
    if not isinstance(tables, list) or not isinstance(profile_tables, list):
        return "Validated profile metadata is unavailable."
    if len(tables) != len(profile_tables):
        return "Profile metadata does not match the normalized tables."
    has_columns = False
    for table, profile_table in zip(tables, profile_tables):
        schema = table.get("schema") if isinstance(table, dict) else None
        columns = (
            profile_table.get("columns")
            if isinstance(profile_table, dict)
            else None
        )
        row_count = table.get("row_count") if isinstance(table, dict) else None
        if (
            not isinstance(schema, list)
            or not isinstance(columns, list)
            or len(schema) != len(columns)
            or isinstance(row_count, bool)
            or not isinstance(row_count, int)
            or row_count < 0
            or profile_table.get("name") != table.get("table_name")
            or isinstance(profile_table.get("row_count"), bool)
            or not isinstance(profile_table.get("row_count"), int)
            or profile_table.get("row_count") != row_count
        ):
            return "Profile metadata does not match the normalized table schema."
        has_columns = has_columns or bool(schema)
        for position, (field, column) in enumerate(zip(schema, columns)):
            if not isinstance(field, dict) or not isinstance(column, dict):
                return "Exact, schema-aligned profile cardinality is unavailable."
            distinct_count = column.get("distinct_value_count")
            missing_count = column.get("missing_value_count")
            empty_count = column.get("empty_value_count")
            if (
                column.get("name") != field.get("name")
                or column.get("position") != position
                or column.get("distinct_count_exact") is not True
                or isinstance(distinct_count, bool)
                or not isinstance(distinct_count, int)
                or distinct_count < 0
                or isinstance(missing_count, bool)
                or not isinstance(missing_count, int)
                or missing_count < 0
                or isinstance(empty_count, bool)
                or not isinstance(empty_count, int)
                or empty_count < 0
                or missing_count + empty_count > row_count
                or distinct_count
                < int(row_count - missing_count > 0)
                or distinct_count > row_count - missing_count
                or empty_count > 0 and distinct_count == 0
            ):
                return "Exact, schema-aligned profile cardinality is unavailable."
    if not has_columns:
        return "No normalized columns are available."
    return None


def _evaluate_constant_column_candidates(
    data: dict,
    _configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for table, profile_table in zip(
        data["tables"],
        data["_profile_result"]["tables"],
    ):
        for position, (field, column) in enumerate(
            zip(table["schema"], profile_table["columns"])
        ):
            budget.consume_comparisons()
            missing_count = column["missing_value_count"]
            empty_count = column["empty_value_count"]
            nonempty_non_null_count = (
                table["row_count"] - missing_count - empty_count
            )
            if nonempty_non_null_count < 2:
                continue
            distinct_nonempty_count = column["distinct_value_count"] - int(
                empty_count > 0
            )
            if distinct_nonempty_count != 1:
                continue
            yield RuleFinding(
                classification="unusual_candidate",
                title="Constant-column candidate",
                description=(
                    f"All {nonempty_non_null_count} non-empty, non-null values "
                    "in this column have one distinct value. This may be "
                    "intentional and is not a validity error."
                ),
                scope={
                    "table_index": table["table_index"],
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=nonempty_non_null_count,
                denominator_basis="non_empty_non_null_values",
                denominator_count=nonempty_non_null_count,
                denominator_reason=None,
                evidence={
                    "distinct_non_empty_non_null_value_count": 1,
                    "empty_values_excluded": True,
                },
                confidence=1.0,
                confidence_basis=(
                    "Exact distinct counts from the validated profile show one "
                    "value among the non-empty, non-null observations."
                ),
                recommended_investigation=(
                    "Confirm whether this field is expected to vary; fixed "
                    "attributes and flags can be legitimate."
                ),
            )


CONSTANT_COLUMN_CANDIDATE_RULE = QualityRule(
    rule_id="generic.constant_column_candidates",
    version="1",
    scope="column",
    severity="info",
    prerequisites=(
        "validated_profile_result",
        "exact_profile_column_cardinality",
        "normalized_column_schema",
    ),
    configuration_defaults={},
    validate_configuration=_empty_rule_configuration,
    unsupported_reason=_constant_columns_unsupported,
    evaluate=_evaluate_constant_column_candidates,
)


def _parquet_paths_unsupported(data: dict, _configuration: dict) -> str | None:
    paths = data.get("_parquet_paths")
    if not isinstance(paths, dict):
        return data.get(
            "_parquet_input_reason",
            "Verified normalized Parquet inputs are unavailable.",
        )
    if data.get("_parquet_input_reason"):
        return data["_parquet_input_reason"]
    total_bytes = 0
    try:
        for table in data["tables"]:
            if not table["schema"]:
                continue
            path = paths.get(table["table_index"])
            if path is None:
                return "Verified normalized Parquet is unavailable for a data table."
            total_bytes += os.path.getsize(path)
            if total_bytes > MAX_RULE_INPUT_BYTES:
                return "Normalized Parquet exceeds the quality-rule input limit."
    except (OSError, TypeError, ValueError):
        return "Verified normalized Parquet inputs are unavailable."
    return None


def _iter_parquet_rows(
    data: dict,
    table: dict,
    selected_columns: list[int],
    budget: RuleBudget,
):
    table_index = table["table_index"]
    path = data["_parquet_paths"].get(table_index)
    if path is None:
        raise ValueError("A normalized Parquet table is unavailable.")
    with pq.ParquetFile(path) as parquet_file:
        row_index = 0
        column_names = [table["schema"][position]["name"] for position in selected_columns]
        for batch in parquet_file.iter_batches(
            batch_size=1,
            columns=column_names,
        ):
            for batch_row in range(batch.num_rows):
                budget.consume_comparisons(max(1, len(selected_columns)))
                yield row_index, [
                    batch.column(column_index)[batch_row].as_py()
                    for column_index in range(batch.num_columns)
                ]
                row_index += 1


def _string_column_positions(table: dict) -> list[int]:
    return [
        position
        for position, field in enumerate(table["schema"])
        if _quality_schema_type(field["type"]) == "string"
    ]


def _string_unsupported(data: dict, configuration: dict) -> str | None:
    reason = _parquet_paths_unsupported(data, configuration)
    if reason is not None:
        return reason
    string_tables = [
        table for table in data["tables"] if _string_column_positions(table)
    ]
    if not string_tables:
        return "No normalized string columns are available."
    if any(
        data["_parquet_paths"].get(table["table_index"]) is None
        for table in string_tables
    ):
        return "Verified normalized Parquet is unavailable for a string table."
    return None


def _evaluate_string_consistency(
    data: dict,
    _configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for table in data["tables"]:
        positions = _string_column_positions(table)
        if not positions:
            continue
        counts = {
            position: {
                "leading_or_trailing_whitespace_count": 0,
                "repeated_whitespace_count": 0,
                "affected_value_count": 0,
                "example_refs": [],
            }
            for position in positions
        }
        for row_index, values in _iter_parquet_rows(data, table, positions, budget):
            for position, value in zip(positions, values):
                if not isinstance(value, str):
                    continue
                leading_trailing = value != value.strip()
                repeated_whitespace = re.search(r"\s{2,}", value) is not None
                if not leading_trailing and not repeated_whitespace:
                    continue
                column_counts = counts[position]
                column_counts["affected_value_count"] += 1
                if leading_trailing:
                    column_counts["leading_or_trailing_whitespace_count"] += 1
                if repeated_whitespace:
                    column_counts["repeated_whitespace_count"] += 1
                if len(column_counts["example_refs"]) < MAX_RULE_EXAMPLES:
                    column_counts["example_refs"].append({"row_index": row_index})
        for position in positions:
            column_counts = counts[position]
            affected = column_counts["affected_value_count"]
            if not affected:
                continue
            field = table["schema"][position]
            yield RuleFinding(
                classification="unusual_candidate",
                title="Whitespace formatting candidates",
                description=(
                    f"{affected} values contain leading, trailing, or repeated "
                    "whitespace. The intended format is not assumed."
                ),
                scope={
                    "table_index": table["table_index"],
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=affected,
                denominator_basis="rows",
                denominator_count=table["row_count"],
                denominator_reason=None,
                evidence={
                    "leading_or_trailing_whitespace_count": column_counts[
                        "leading_or_trailing_whitespace_count"
                    ],
                    "repeated_whitespace_count": column_counts[
                        "repeated_whitespace_count"
                    ],
                    "example_refs": column_counts["example_refs"],
                },
                confidence=1.0,
                confidence_basis=(
                    "String values were inspected without changing their "
                    "stored representation."
                ),
                recommended_investigation=(
                    "Confirm the expected whitespace convention before "
                    "changing or rejecting any values."
                ),
            )


STRING_CONSISTENCY_RULE = QualityRule(
    rule_id="generic.string_whitespace_candidates",
    version="1",
    scope="column",
    severity="info",
    prerequisites=("verified_normalized_parquet", "normalized_string_columns"),
    configuration_defaults={},
    validate_configuration=_empty_rule_configuration,
    unsupported_reason=_string_unsupported,
    evaluate=_evaluate_string_consistency,
)


def _numeric_column_positions(table: dict) -> list[int]:
    return [
        position
        for position, field in enumerate(table["schema"])
        if _quality_schema_type(field["type"]) in {"integer", "number"}
    ]


def _numeric_unsupported(data: dict, configuration: dict) -> str | None:
    reason = _parquet_paths_unsupported(data, configuration)
    if reason is not None:
        return reason
    numeric_tables = [
        table for table in data["tables"] if _numeric_column_positions(table)
    ]
    if not numeric_tables:
        return "No normalized numeric columns are available."
    if any(
        data["_parquet_paths"].get(table["table_index"]) is None
        for table in numeric_tables
    ):
        return "Verified normalized Parquet is unavailable for a numeric table."
    return None


def _validate_numeric_outlier_configuration(configuration: dict) -> dict:
    if not isinstance(configuration, dict) or set(configuration) != {
        "method",
        "threshold",
        "min_samples",
    }:
        raise QualityRuleConfigurationError(
            "Numeric outlier configuration has unsupported fields."
        )
    method = configuration["method"]
    threshold = configuration["threshold"]
    min_samples = configuration["min_samples"]
    if (
        not isinstance(method, str)
        or method not in {"iqr", "z_score"}
        or isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold <= 0
        or threshold > 100
        or isinstance(min_samples, bool)
        or not isinstance(min_samples, int)
        or not 2 <= min_samples <= 1_000_000
    ):
        raise QualityRuleConfigurationError(
            "Numeric outlier method, threshold, or minimum sample count is invalid."
        )
    return {
        "method": method,
        "threshold": float(threshold),
        "min_samples": min_samples,
    }


def _finite_decimal(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(str(value))
    return None


def _quartile(sorted_values: list[Decimal], fraction: Decimal) -> Decimal:
    position = Decimal(len(sorted_values) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    return (
        sorted_values[lower_index]
        + (sorted_values[upper_index] - sorted_values[lower_index]) * weight
    )


def _z_score_statistics(
    data: dict,
    table: dict,
    position: int,
    budget: RuleBudget,
) -> tuple[int, Decimal, Decimal]:
    count = 0
    mean = Decimal(0)
    sum_squared_deviations = Decimal(0)
    with localcontext() as context:
        context.prec = 100
        for _row_index, values in _iter_parquet_rows(
            data,
            table,
            [position],
            budget,
        ):
            value = _finite_decimal(values[0])
            if value is None:
                continue
            count += 1
            delta = value - mean
            mean += delta / count
            sum_squared_deviations += delta * (value - mean)
        if count < 2:
            return count, mean, Decimal(0)
        variance = sum_squared_deviations / count
        return count, mean, max(variance, Decimal(0)).sqrt()


def _evaluate_numeric_outliers(
    data: dict,
    configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    threshold = Decimal(str(configuration["threshold"]))
    for table in data["tables"]:
        for position in _numeric_column_positions(table):
            if configuration["method"] == "iqr":
                values = []
                for row_index, row in _iter_parquet_rows(
                    data,
                    table,
                    [position],
                    budget,
                ):
                    numeric = _finite_decimal(row[0])
                    if numeric is None:
                        continue
                    numeric_size = len(str(numeric).encode("utf-8"))
                    if numeric_size > MAX_OUTLIER_VALUE_BYTES:
                        raise RuleBudgetExhausted
                    budget.consume_memory(numeric_size + 384)
                    values.append((numeric, row_index))
                if len(values) < configuration["min_samples"]:
                    continue
                with localcontext() as context:
                    context.prec = 100
                    ordered = sorted(value for value, _row_index in values)
                    first_quartile = _quartile(ordered, Decimal("0.25"))
                    third_quartile = _quartile(ordered, Decimal("0.75"))
                    spread = third_quartile - first_quartile
                    lower_bound = first_quartile - threshold * spread
                    upper_bound = third_quartile + threshold * spread
                    candidate_count = 0
                    example_refs = []
                    for value, row_index in values:
                        if value < lower_bound or value > upper_bound:
                            candidate_count += 1
                            if len(example_refs) < MAX_RULE_EXAMPLES:
                                example_refs.append({"row_index": row_index})
            else:
                count, mean, standard_deviation = _z_score_statistics(
                    data,
                    table,
                    position,
                    budget,
                )
                if (
                    count < configuration["min_samples"]
                    or standard_deviation == 0
                ):
                    continue
                lower_bound = mean - threshold * standard_deviation
                upper_bound = mean + threshold * standard_deviation
                candidate_count = 0
                example_refs = []
                for row_index, row in _iter_parquet_rows(
                    data,
                    table,
                    [position],
                    budget,
                ):
                    numeric = _finite_decimal(row[0])
                    if numeric is not None and (
                        numeric < lower_bound or numeric > upper_bound
                    ):
                        candidate_count += 1
                        if len(example_refs) < MAX_RULE_EXAMPLES:
                            example_refs.append({"row_index": row_index})
            if not candidate_count:
                continue
            field = table["schema"][position]
            yield RuleFinding(
                classification="unusual_candidate",
                title="Statistical numeric outlier candidates",
                description=(
                    f"{candidate_count} finite values fall beyond the "
                    f"configured {configuration['method']} candidate bounds. "
                    "This statistical signal does not establish an error."
                ),
                scope={
                    "table_index": table["table_index"],
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=candidate_count,
                denominator_basis="finite_numeric_values",
                denominator_count=(
                    len(values)
                    if configuration["method"] == "iqr"
                    else count
                ),
                denominator_reason=None,
                evidence={
                    "method": configuration["method"],
                    "threshold": configuration["threshold"],
                    "lower_bound": str(lower_bound)[:_TEXT_LIMIT],
                    "upper_bound": str(upper_bound)[:_TEXT_LIMIT],
                    "minimum_sample_count": configuration["min_samples"],
                    "example_refs": example_refs,
                },
                confidence=1.0,
                confidence_basis=(
                    "The rule applies only the configured deterministic "
                    "statistical method to finite normalized numeric values; "
                    "it does not infer business validity."
                ),
                recommended_investigation=(
                    "Review candidate values in their business context; "
                    "do not remove or alter them based on this statistical signal."
                ),
            )


NUMERIC_OUTLIER_CANDIDATE_RULE = QualityRule(
    rule_id="generic.numeric_outlier_candidates",
    version="1",
    scope="column",
    severity="info",
    prerequisites=("verified_normalized_parquet", "normalized_numeric_columns"),
    configuration_defaults={
        "method": "iqr",
        "threshold": 1.5,
        "min_samples": 4,
    },
    validate_configuration=_validate_numeric_outlier_configuration,
    unsupported_reason=_numeric_unsupported,
    evaluate=_evaluate_numeric_outliers,
)


def _evaluate_numeric_validity(
    data: dict,
    _configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for table in data["tables"]:
        positions = _numeric_column_positions(table)
        if not positions:
            continue
        counts = {
            position: {
                "non_finite_count": 0,
                "structurally_invalid_count": 0,
                "numeric_value_count": 0,
                "example_refs": [],
            }
            for position in positions
        }
        for row_index, values in _iter_parquet_rows(data, table, positions, budget):
            for position, value in zip(positions, values):
                column_counts = counts[position]
                if value is None:
                    continue
                column_counts["numeric_value_count"] += 1
                non_finite = (
                    isinstance(value, float) and not math.isfinite(value)
                ) or (isinstance(value, Decimal) and not value.is_finite())
                field_type = _quality_schema_type(table["schema"][position]["type"])
                valid_structure = (
                    not isinstance(value, bool)
                    and (
                        field_type == "integer"
                        and isinstance(value, int)
                        or field_type == "number"
                        and isinstance(value, (int, float, Decimal))
                    )
                )
                if non_finite:
                    column_counts["non_finite_count"] += 1
                elif not valid_structure:
                    column_counts["structurally_invalid_count"] += 1
                else:
                    continue
                if len(column_counts["example_refs"]) < MAX_RULE_EXAMPLES:
                    column_counts["example_refs"].append({"row_index": row_index})
        for position in positions:
            column_counts = counts[position]
            affected = (
                column_counts["non_finite_count"]
                + column_counts["structurally_invalid_count"]
            )
            if not affected:
                continue
            field = table["schema"][position]
            yield RuleFinding(
                classification="suspected_issue",
                title="Numeric validity candidates",
                description=(
                    f"{affected} values are non-finite or do not match the "
                    "normalized numeric structure."
                ),
                scope={
                    "table_index": table["table_index"],
                    "table_name": table.get("table_name"),
                    "column_position": position,
                    "column_name": field["name"],
                },
                affected_count=affected,
                denominator_basis="non_null_numeric_values",
                denominator_count=column_counts["numeric_value_count"],
                denominator_reason=None,
                evidence={
                    "non_finite_count": column_counts["non_finite_count"],
                    "structurally_invalid_count": column_counts[
                        "structurally_invalid_count"
                    ],
                    "example_refs": column_counts["example_refs"],
                },
                confidence=1.0,
                confidence_basis=(
                    "Values were read from the validated normalized Parquet "
                    "schema; no threshold or negative-value policy is applied."
                ),
                recommended_investigation=(
                    "Inspect candidate values and confirm whether they are "
                    "representable in the intended numeric domain."
                ),
            )


NUMERIC_VALIDITY_RULE = QualityRule(
    rule_id="generic.numeric_validity_candidates",
    version="1",
    scope="column",
    severity="medium",
    prerequisites=("verified_normalized_parquet", "normalized_numeric_columns"),
    configuration_defaults={},
    validate_configuration=_empty_rule_configuration,
    unsupported_reason=_numeric_unsupported,
    evaluate=_evaluate_numeric_validity,
)


def _exact_duplicates_unsupported(data: dict, configuration: dict) -> str | None:
    reason = _parquet_paths_unsupported(data, configuration)
    if reason is not None:
        return reason
    if any(
        table["schema"]
        and data["_parquet_paths"].get(table["table_index"]) is None
        for table in data["tables"]
    ):
        return "Verified normalized Parquet is unavailable for an exact-row scan."
    return None


def _evaluate_exact_duplicates(
    data: dict,
    _configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for table_index, table in enumerate(data["tables"]):
        positions = list(range(len(table["schema"])))
        if not positions:
            continue
        seen = set()
        duplicate_count = 0
        for _row_index, values in _iter_parquet_rows(
            data,
            table,
            positions,
            budget,
        ):
            if _bounded_value_size(values, MAX_EXACT_ROW_VALUE_BYTES) > (
                MAX_EXACT_ROW_VALUE_BYTES
            ):
                raise RuleBudgetExhausted
            fingerprint = _row_fingerprint(values)
            if fingerprint in seen:
                duplicate_count += 1
            else:
                budget.consume_memory(len(fingerprint) + 72)
                seen.add(fingerprint)
        if duplicate_count == 0:
            continue
        yield RuleFinding(
            classification="unusual_candidate",
            title="Exact duplicate row candidates",
            description=(
                f"The validated profile found {duplicate_count} repeated "
                "complete rows. Repeated rows may be intentional."
            ),
            scope={
                "table_index": table_index,
                "table_name": table.get("table_name"),
                "column_position": None,
                "column_name": None,
            },
            affected_count=duplicate_count,
            denominator_basis="rows",
            denominator_count=table["row_count"],
            denominator_reason=None,
            evidence={
                "duplicate_row_count": duplicate_count,
                "exact_count": True,
            },
            confidence=1.0,
            confidence_basis=(
                "The parser profile reports an exact duplicate-row count "
                "within its configured tracking budget."
            ),
            recommended_investigation=(
                "Confirm whether repeated complete rows are expected; do not "
                "delete or merge them automatically."
            ),
        )


EXACT_DUPLICATE_RULE = QualityRule(
    rule_id="generic.exact_duplicate_rows",
    version="1",
    scope="table",
    severity="info",
    prerequisites=(
        "validated_exact_duplicate_profile",
        "verified_normalized_parquet",
    ),
    configuration_defaults={},
    validate_configuration=_empty_rule_configuration,
    unsupported_reason=_exact_duplicates_unsupported,
    evaluate=_evaluate_exact_duplicates,
)


def _business_key_configuration(configuration: dict) -> dict:
    if not isinstance(configuration, dict) or set(configuration) != {"keys"}:
        raise QualityRuleConfigurationError(
            "Business-key rule configuration must contain only 'keys'."
        )
    keys = configuration["keys"]
    if not isinstance(keys, list) or len(keys) > 255:
        raise QualityRuleConfigurationError("Business-key definitions are invalid.")
    table_indexes = set()
    for item in keys:
        if (
            not isinstance(item, dict)
            or set(item) != {"table_index", "columns"}
            or isinstance(item["table_index"], bool)
            or not isinstance(item["table_index"], int)
            or item["table_index"] < 0
            or item["table_index"] in table_indexes
            or not isinstance(item["columns"], list)
            or not item["columns"]
            or len(item["columns"]) > 64
            or any(
                not isinstance(column, str)
                or not column.strip()
                or len(column) > _TEXT_LIMIT
                for column in item["columns"]
            )
            or len(set(item["columns"])) != len(item["columns"])
            or sum(len(column) for column in item["columns"])
            + max(0, len(item["columns"]) - 1) * 2
            > _TEXT_LIMIT
        ):
            raise QualityRuleConfigurationError(
                "Each business-key definition needs unique table and column names."
            )
        table_indexes.add(item["table_index"])
    return {"keys": copy.deepcopy(keys)}


def _business_key_unsupported(data: dict, configuration: dict) -> str | None:
    if not configuration["keys"]:
        return "No explicit business-key definition was supplied."
    reason = _parquet_paths_unsupported(data, configuration)
    if reason is not None:
        return reason
    for item in configuration["keys"]:
        if item["table_index"] >= len(data["tables"]):
            return "A configured business-key table does not exist."
        schema_names = {
            field["name"] for field in data["tables"][item["table_index"]]["schema"]
        }
        if set(item["columns"]) - schema_names:
            return "A configured business-key column does not exist."
    return None


def _business_key_token(values: list) -> bytes:
    if _bounded_value_size(values, MAX_BUSINESS_KEY_VALUE_BYTES) > (
        MAX_BUSINESS_KEY_VALUE_BYTES
    ):
        raise RuleBudgetExhausted
    result = canonical_json(_normalize(values))
    if len(result) > MAX_BUSINESS_KEY_VALUE_BYTES:
        raise RuleBudgetExhausted
    return result


def _bounded_value_size(value, limit: int) -> int:
    if value is None or isinstance(value, (bool, int, float, Decimal)):
        return 32
    if isinstance(value, str):
        if len(value) > limit:
            return limit + 1
        return len(value.encode("utf-8")) + 8
    if isinstance(value, bytes):
        return len(value) + 8
    if hasattr(value, "isoformat"):
        return min(len(value.isoformat()), limit + 1) + 8
    if isinstance(value, (list, tuple)):
        total = 8
        for item in value:
            total += _bounded_value_size(item, limit - total)
            if total > limit:
                return total
        return total
    if isinstance(value, dict):
        total = 8
        for key, item in value.items():
            total += _bounded_value_size(str(key), limit - total)
            total += _bounded_value_size(item, limit - total)
            if total > limit:
                return total
        return total
    return min(len(str(value)), limit + 1) + 8


def _evaluate_business_key_duplicates(
    data: dict,
    configuration: dict,
    budget: RuleBudget,
) -> Iterable[RuleFinding]:
    for key_definition in configuration["keys"]:
        table = data["tables"][key_definition["table_index"]]
        positions = [
            next(
                position
                for position, field in enumerate(table["schema"])
                if field["name"] == name
            )
            for name in key_definition["columns"]
        ]
        seen = set()
        duplicate_count = 0
        null_key_count = 0
        examples = []
        for row_index, values in _iter_parquet_rows(data, table, positions, budget):
            key_values = values
            if any(value is None for value in key_values):
                null_key_count += 1
                continue
            fingerprint = _business_key_token(key_values)
            if fingerprint in seen:
                duplicate_count += 1
                if len(examples) < MAX_RULE_EXAMPLES:
                    examples.append({"row_index": row_index})
            else:
                budget.consume_memory(len(fingerprint) + 72)
                seen.add(fingerprint)
        if duplicate_count == 0:
            continue
        yield RuleFinding(
            classification="unusual_candidate",
            title="Possible business-key duplicate candidates",
            description=(
                f"{duplicate_count} rows repeat the explicitly configured "
                "business-key values. Uniqueness is not assumed."
            ),
            scope={
                "table_index": key_definition["table_index"],
                "table_name": table.get("table_name"),
                "column_position": None,
                "column_name": None,
            },
            affected_count=duplicate_count,
            denominator_basis="rows_with_non_null_key",
            denominator_count=table["row_count"] - null_key_count,
            denominator_reason=None,
            evidence={
                "key_columns": ", ".join(key_definition["columns"]),
                "duplicate_key_row_count": duplicate_count,
                "rows_with_null_key_values": null_key_count,
                "example_refs": examples,
            },
            confidence=1.0,
            confidence_basis=(
                "The comparison uses only the explicitly configured key "
                "columns and exact typed value matches."
            ),
            recommended_investigation=(
                "Confirm whether the configured key is required to be unique "
                "before treating repeated keys as errors."
            ),
        )


BUSINESS_KEY_DUPLICATE_RULE = QualityRule(
    rule_id="configured.business_key_duplicate_candidates",
    version="1",
    scope="table",
    severity="low",
    prerequisites=("verified_normalized_parquet", "explicit_business_key_definition"),
    configuration_defaults={"keys": []},
    validate_configuration=_business_key_configuration,
    unsupported_reason=_business_key_unsupported,
    evaluate=_evaluate_business_key_duplicates,
)


class QualityRuleRegistry:
    def __init__(self, rules: Iterable[QualityRule]):
        ordered_rules = sorted(rules, key=lambda rule: rule.rule_id)
        if len(ordered_rules) > MAX_RULES:
            raise QualityRuleConfigurationError(
                "The quality rule registry exceeds its rule limit."
            )
        if len({rule.rule_id for rule in ordered_rules}) != len(ordered_rules):
            raise QualityRuleConfigurationError(
                "The quality rule registry contains duplicate identifiers."
            )
        for rule in ordered_rules:
            self._validate_rule(rule)
        self._rules = tuple(ordered_rules)

    @staticmethod
    def _validate_rule(rule: QualityRule) -> None:
        if (
            not isinstance(rule.rule_id, str)
            or _RULE_ID.fullmatch(rule.rule_id) is None
            or len(rule.rule_id) > 480
            or not isinstance(rule.version, str)
            or _RULE_VERSION.fullmatch(rule.version) is None
            or len(rule.version) > 32
            or not isinstance(rule.scope, str)
            or rule.scope not in {"table", "column"}
            or not isinstance(rule.severity, str)
            or rule.severity not in _SEVERITIES
            or not isinstance(rule.prerequisites, tuple)
            or not rule.prerequisites
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > _TEXT_LIMIT
                for item in rule.prerequisites
            )
            or not callable(rule.validate_configuration)
            or not callable(rule.unsupported_reason)
            or not callable(rule.evaluate)
            or not isinstance(rule.configuration_defaults, dict)
        ):
            raise QualityRuleConfigurationError(
                "A quality rule definition is invalid."
            )
        catalog_entry = get_quality_rule_catalog_entry(rule.rule_id)
        if catalog_entry is not None:
            if catalog_entry["status"] != "implemented":
                raise QualityRuleConfigurationError(
                    f"Catalog rule {rule.rule_id!r} is not executable."
                )
            if _IMPLEMENTED_EXECUTORS.get(rule.rule_id) is not rule:
                raise QualityRuleConfigurationError(
                    f"Catalog rule {rule.rule_id!r} is bound to a different executor."
                )
            if (
                rule.version != catalog_entry["version"]
                or rule.scope != catalog_entry["scope"]
                or rule.severity != catalog_entry["severity"]
                or list(rule.prerequisites) != catalog_entry["prerequisites"]
                or rule.configuration_defaults
                != catalog_entry["default_configuration"]
            ):
                raise QualityRuleConfigurationError(
                    f"Executor metadata for catalog rule {rule.rule_id!r} does not match."
                )
        try:
            canonical_json(rule.configuration_defaults)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise QualityRuleConfigurationError(
                "Quality rule defaults must be bounded JSON-compatible values."
            ) from error
        if (
            len(canonical_json(rule.configuration_defaults))
            > _CONFIGURATION_BYTES_LIMIT
        ):
            raise QualityRuleConfigurationError(
                "Quality rule defaults exceed the configuration size limit."
            )

    def __iter__(self):
        return iter(self._rules)

    def __len__(self) -> int:
        return len(self._rules)


_IMPLEMENTED_EXECUTORS = {
    rule.rule_id: rule
    for rule in (
        BUSINESS_KEY_DUPLICATE_RULE,
        CONSTANT_COLUMN_CANDIDATE_RULE,
        EXACT_DUPLICATE_RULE,
        MISSING_VALUE_CANDIDATE_RULE,
        NUMERIC_OUTLIER_CANDIDATE_RULE,
        NUMERIC_VALIDITY_RULE,
        STRING_CONSISTENCY_RULE,
        TYPE_CONSISTENCY_RULE,
    )
}
_CATALOG_IMPLEMENTED_IDS = {
    entry["rule_id"]
    for entry in quality_rule_catalog_entries()
    if entry["status"] == "implemented"
}
if _CATALOG_IMPLEMENTED_IDS != set(_IMPLEMENTED_EXECUTORS):
    raise QualityRuleConfigurationError(
        "The quality-rule catalog and implemented executor registry do not match."
    )
DEFAULT_QUALITY_RULES = QualityRuleRegistry(
    _IMPLEMENTED_EXECUTORS[entry["rule_id"]]
    for entry in quality_rule_catalog_entries()
    if entry["status"] == "implemented"
)
_DEFAULT_ENABLED_CATALOG = {
    entry["rule_id"]: entry
    for entry in quality_rule_catalog_entries()
    if entry["status"] == "implemented" and entry["default_enabled"]
}
QUALITY_RULE_ENGINE_CONFIGURATION = {
    "candidate_comparisons": 10_000,
    "enabled_rules": sorted(_DEFAULT_ENABLED_CATALOG),
    "max_examples_per_finding": MAX_RULE_EXAMPLES,
    "max_findings": MAX_RULE_FINDINGS,
    "max_report_bytes": MAX_RULE_RESULT_BYTES,
    "rule_versions": {
        rule_id: entry["version"]
        for rule_id, entry in _DEFAULT_ENABLED_CATALOG.items()
    },
    "rules": {
        rule_id: copy.deepcopy(entry["default_configuration"])
        for rule_id, entry in _DEFAULT_ENABLED_CATALOG.items()
    },
}


def _validate_finding(
    finding: RuleFinding,
    rule: QualityRule,
    max_examples: int,
) -> None:
    if not isinstance(finding, RuleFinding):
        raise ValueError("The rule returned an invalid finding.")
    text_fields = (
        finding.title,
        finding.description,
        finding.denominator_basis,
        finding.confidence_basis,
        finding.recommended_investigation,
    )
    if (
        finding.classification not in _CLASSIFICATIONS
        or any(
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _TEXT_LIMIT
            for value in text_fields
        )
        or not isinstance(finding.scope, dict)
        or set(finding.scope)
        != {"table_index", "table_name", "column_position", "column_name"}
        or any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            )
            for value in (
                finding.scope["table_index"],
                finding.scope["column_position"],
            )
        )
        or any(
            value is not None
            and (
                not isinstance(value, str)
                or len(value) > _TEXT_LIMIT
            )
            for value in (finding.scope["table_name"], finding.scope["column_name"])
        )
        or rule.scope == "column"
        and (
            finding.scope["table_index"] is None
            or finding.scope["column_position"] is None
        )
        or isinstance(finding.affected_count, bool)
        or not isinstance(finding.affected_count, int)
        or finding.affected_count < 0
        or finding.denominator_count is not None
        and (
            isinstance(finding.denominator_count, bool)
            or not isinstance(finding.denominator_count, int)
            or finding.denominator_count < 0
            or finding.affected_count > finding.denominator_count
        )
        or finding.denominator_count is None
        and (
            not isinstance(finding.denominator_reason, str)
            or not finding.denominator_reason.strip()
            or len(finding.denominator_reason) > _TEXT_LIMIT
        )
        or isinstance(finding.confidence, bool)
        or not isinstance(finding.confidence, (int, float))
        or not 0 <= finding.confidence <= 1
        or not isinstance(finding.evidence, dict)
        or not isinstance(finding.evidence.get("example_refs", []), list)
        or len(finding.evidence.get("example_refs", [])) > max_examples
    ):
        raise ValueError("The rule returned an invalid finding.")
    for reference in finding.evidence.get("example_refs", []):
        if (
            not isinstance(reference, dict)
            or set(reference) != {"row_index"}
            or isinstance(reference["row_index"], bool)
            or not isinstance(reference["row_index"], int)
            or reference["row_index"] < 0
        ):
            raise ValueError("The rule returned an invalid example reference.")
    if any(
        not isinstance(key, str)
        or not key.strip()
        or len(key) > _TEXT_LIMIT
        or key == "example_refs"
        or not (
            value is None
            or isinstance(value, (bool, int))
            or isinstance(value, float)
            and math.isfinite(value)
            or isinstance(value, str)
            and len(value) <= _TEXT_LIMIT
        )
        or isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > 2**63 - 1
        for key, value in finding.evidence.items()
        if key != "example_refs"
    ):
        raise ValueError("The rule returned unbounded or unsupported evidence.")
    if len(canonical_json(finding.evidence)) > _CONFIGURATION_BYTES_LIMIT:
        raise ValueError("The rule returned oversized evidence.")


def _scope_sort_key(finding: RuleFinding) -> tuple:
    return (
        finding.scope["table_index"] if finding.scope["table_index"] is not None else -1,
        finding.scope["column_position"]
        if finding.scope["column_position"] is not None
        else -1,
        finding.scope["table_name"] or "",
        finding.scope["column_name"] or "",
    )


def run_quality_rules(
    normalization_result: dict,
    *,
    configuration: dict | None = None,
    registry: QualityRuleRegistry | None = None,
) -> list[RuleExecutionResult]:
    active_registry = registry if registry is not None else DEFAULT_QUALITY_RULES
    supplied = (
        copy.deepcopy(QUALITY_RULE_ENGINE_CONFIGURATION)
        if configuration is None
        else configuration
    )
    if not isinstance(supplied, dict) or set(supplied) != set(
        QUALITY_RULE_ENGINE_CONFIGURATION
    ):
        raise QualityRuleConfigurationError(
            "Quality rule engine configuration has unsupported fields."
        )
    for name in (
        "candidate_comparisons",
        "max_examples_per_finding",
        "max_findings",
        "max_report_bytes",
    ):
        value = supplied[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > QUALITY_RULE_ENGINE_CONFIGURATION[name]
        ):
            raise QualityRuleConfigurationError(
                f"Quality rule limit {name!r} is invalid or exceeds its maximum."
            )
    rule_configurations = supplied["rules"]
    if not isinstance(rule_configurations, dict):
        raise QualityRuleConfigurationError(
            "Rule configurations must be provided as an object."
        )
    rules_by_id = {rule.rule_id: rule for rule in active_registry}
    enabled_rule_ids = supplied["enabled_rules"]
    if (
        not isinstance(enabled_rule_ids, list)
        or any(not isinstance(rule_id, str) for rule_id in enabled_rule_ids)
        or enabled_rule_ids != sorted(set(enabled_rule_ids))
        or set(enabled_rule_ids) - rules_by_id.keys()
    ):
        raise QualityRuleConfigurationError(
            "Enabled quality rules must be unique, sorted, and registered."
        )
    if set(rule_configurations) != set(enabled_rule_ids):
        raise QualityRuleConfigurationError(
            "Every enabled quality rule needs an explicit configuration."
        )
    configured_versions = supplied["rule_versions"]
    if (
        not isinstance(configured_versions, dict)
        or set(configured_versions) != set(enabled_rule_ids)
        or any(
            configured_versions[rule_id] != rule.version
            for rule_id, rule in rules_by_id.items()
            if rule_id in enabled_rule_ids
        )
    ):
        raise QualityRuleConfigurationError(
            "Configured quality rule versions do not match the registry."
        )

    selected_rules = tuple(
        rule for rule in active_registry if rule.rule_id in enabled_rule_ids
    )
    resolved_configurations = {}
    for rule in selected_rules:
        raw_configuration = copy.deepcopy(rule_configurations[rule.rule_id])
        try:
            validate_quality_rule_configuration(
                rule.rule_id,
                raw_configuration,
            )
            validated = rule.validate_configuration(raw_configuration)
        except QualityRuleConfigurationError:
            raise
        except Exception as error:
            raise QualityRuleConfigurationError(
                f"Configuration for rule {rule.rule_id!r} is invalid."
            ) from error
        if not isinstance(validated, dict):
            raise QualityRuleConfigurationError(
                f"Configuration for rule {rule.rule_id!r} is invalid."
            )
        try:
            validated_bytes = canonical_json(validated)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise QualityRuleConfigurationError(
                f"Configuration for rule {rule.rule_id!r} is invalid."
            ) from error
        if len(validated_bytes) > _CONFIGURATION_BYTES_LIMIT:
            raise QualityRuleConfigurationError(
                f"Configuration for rule {rule.rule_id!r} is too large."
            )
        resolved_configurations[rule.rule_id] = validated

    budget = RuleBudget(supplied["candidate_comparisons"])
    max_findings = supplied["max_findings"]
    max_examples = supplied["max_examples_per_finding"]
    max_result_bytes = supplied["max_report_bytes"]
    results = []
    finding_count = 0
    result_bytes = 0
    result_budget_exhausted = False
    for rule in selected_rules:
        rule_config = resolved_configurations[rule.rule_id]
        if result_budget_exhausted:
            results.append(
                RuleExecutionResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status="BudgetExhausted",
                    severity=rule.severity,
                    findings=(),
                    affected_count=0,
                    denominator_count=None,
                    rows_considered=0,
                    budget_limit=budget.limit,
                    budget_used=budget.used,
                    reason="The quality report result-size limit was reached.",
                )
            )
            continue
        try:
            unsupported_reason = rule.unsupported_reason(
                normalization_result,
                rule_config,
            )
        except Exception as error:
            logger.error(
                "Quality rule %s failed while checking prerequisites (%s)",
                rule.rule_id,
                type(error).__name__,
            )
            results.append(
                RuleExecutionResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status="Failed",
                    severity=rule.severity,
                    findings=(),
                    affected_count=0,
                    denominator_count=None,
                    rows_considered=0,
                    budget_limit=budget.limit,
                    budget_used=budget.used,
                    reason=(
                        "This rule failed while checking its prerequisites; "
                        "inspect the rule log before relying on its results."
                    ),
                )
            )
            continue
        if unsupported_reason is not None:
            if (
                not isinstance(unsupported_reason, str)
                or not unsupported_reason.strip()
            ):
                results.append(
                    RuleExecutionResult(
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        status="Failed",
                        severity=rule.severity,
                        findings=(),
                        affected_count=0,
                        denominator_count=None,
                        rows_considered=0,
                        budget_limit=budget.limit,
                        budget_used=budget.used,
                        reason=(
                            "This rule returned an invalid prerequisite reason; "
                            "inspect the rule log before relying on its results."
                        ),
                    )
                )
                continue
            results.append(
                RuleExecutionResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status="NotApplicable",
                    severity=rule.severity,
                    findings=(),
                    affected_count=0,
                    denominator_count=None,
                    rows_considered=0,
                    budget_limit=budget.limit,
                    budget_used=budget.used,
                    reason=unsupported_reason[:_TEXT_LIMIT],
                )
            )
            continue

        rule_findings = []
        seen_scopes = set()
        rule_result_bytes = 0
        try:
            for finding in rule.evaluate(
                normalization_result,
                rule_config,
                budget,
            ):
                _validate_finding(finding, rule, max_examples)
                scope_identity = canonical_json(finding.scope)
                if scope_identity in seen_scopes:
                    raise ValueError("The rule returned a duplicate finding scope.")
                if finding_count + len(rule_findings) >= max_findings:
                    raise RuleBudgetExhausted
                finding_bytes = len(
                    canonical_json(
                        {
                            "rule_id": rule.rule_id,
                            "rule_version": rule.version,
                            "classification": finding.classification,
                            "title": finding.title,
                            "description": finding.description,
                            "scope": finding.scope,
                            "evidence": {
                                "observed": {
                                    **{
                                        key: value
                                        for key, value in finding.evidence.items()
                                        if key != "example_refs"
                                    },
                                    "affected_count": finding.affected_count,
                                },
                                "example_refs": finding.evidence.get(
                                    "example_refs",
                                    [],
                                ),
                            },
                            "denominator": {
                                "basis": finding.denominator_basis,
                                "count": finding.denominator_count,
                                "reason": finding.denominator_reason,
                            },
                            "severity": rule.severity,
                            "confidence": finding.confidence,
                            "confidence_basis": finding.confidence_basis,
                            "recommended_investigation": (
                                finding.recommended_investigation
                            ),
                        }
                    )
                )
                if (
                    result_bytes + rule_result_bytes + finding_bytes
                    > max_result_bytes // 2
                ):
                    result_budget_exhausted = True
                    raise RuleBudgetExhausted
                rule_findings.append(finding)
                seen_scopes.add(scope_identity)
                rule_result_bytes += finding_bytes
        except RuleBudgetExhausted:
            results.append(
                RuleExecutionResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status="BudgetExhausted",
                    severity=rule.severity,
                    findings=(),
                    affected_count=0,
                    denominator_count=None,
                    rows_considered=0,
                    budget_limit=budget.limit,
                    budget_used=budget.used,
                    reason="A configured comparison, findings, or result-size limit was reached.",
                )
            )
            continue
        except Exception as error:
            logger.error(
                "Quality rule %s failed (%s)",
                rule.rule_id,
                type(error).__name__,
            )
            results.append(
                RuleExecutionResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status="Failed",
                    severity=rule.severity,
                    findings=(),
                    affected_count=0,
                    denominator_count=None,
                    rows_considered=0,
                    budget_limit=budget.limit,
                    budget_used=budget.used,
                    reason=(
                        "This rule failed during execution; inspect the rule "
                        "log before relying on its results."
                    ),
                )
            )
            continue

        ordered_findings = tuple(sorted(rule_findings, key=_scope_sort_key))
        finding_count += len(ordered_findings)
        result_bytes += rule_result_bytes
        rows_considered = sum(
            table["row_count"] for table in normalization_result["tables"]
        )
        denominator_count = (
            sum(table["row_count"] for table in normalization_result["tables"])
            if normalization_result["tables"]
            else 0
        )
        results.append(
            RuleExecutionResult(
                rule_id=rule.rule_id,
                rule_version=rule.version,
                status="Completed",
                severity=rule.severity,
                findings=ordered_findings,
                affected_count=sum(
                    finding.affected_count for finding in ordered_findings
                ),
                denominator_count=denominator_count,
                rows_considered=rows_considered,
                budget_limit=budget.limit,
                budget_used=budget.used,
                reason=None,
            )
        )
    return results
