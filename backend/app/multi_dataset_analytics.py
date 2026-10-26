from __future__ import annotations

import math
import re
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal

import pyarrow as pa


MAX_ANALYTICS_SOURCES = 4
MAX_ANALYTICS_ROWS = 100_000
MAX_ANALYTICS_COLUMNS = 128
MAX_ANALYTICS_GROUPS = 5_000
MAX_ANALYTICS_DISTINCT_VALUES = 250_000
MAX_ANALYTICS_DECODED_BYTES = 32 * 1024 * 1024
MAX_ANALYTICS_JOIN_BYTES = 64 * 1024 * 1024
MAX_RECOMMENDATIONS = 50
MAX_PROFILE_ROWS = 1_000
_ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


class MultiDatasetAnalyticsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AnalyticsSource:
    alias: str
    dataset_id: int
    file_id: int
    table_index: int
    table: pa.Table
    checksum: str


@dataclass(frozen=True)
class JoinKey:
    source_alias: str
    column: str


@dataclass(frozen=True)
class JoinStep:
    left: JoinKey
    right: JoinKey
    join_type: Literal["inner", "left"]
    allow_many_to_many: bool = False


@dataclass(frozen=True)
class Metric:
    name: str
    operation: Literal[
        "count",
        "sum",
        "average",
        "minimum",
        "maximum",
        "distinct_count",
    ]
    column: JoinKey | None = None


@dataclass
class _Frame:
    aliases: set[str]
    names: list[str]
    rows: list[tuple[Any, ...]]
    types: dict[str, pa.DataType]
    estimated_bytes: int


def _validate_sources(sources: list[AnalyticsSource]) -> None:
    if not isinstance(sources, list) or not 2 <= len(sources) <= MAX_ANALYTICS_SOURCES:
        raise MultiDatasetAnalyticsError(
            "invalid_source_count",
            f"Select 2 to {MAX_ANALYTICS_SOURCES} dataset files.",
        )
    aliases: set[str] = set()
    total_rows = 0
    total_bytes = 0
    for source in sources:
        if not isinstance(source, AnalyticsSource):
            raise MultiDatasetAnalyticsError("invalid_source", "A dataset source is invalid.")
        if not _ALIAS_PATTERN.fullmatch(source.alias) or source.alias in aliases:
            raise MultiDatasetAnalyticsError(
                "invalid_source_alias",
                "Source aliases must be unique, bounded identifiers.",
            )
        if (
            isinstance(source.dataset_id, bool)
            or not isinstance(source.dataset_id, int)
            or source.dataset_id < 1
            or isinstance(source.file_id, bool)
            or not isinstance(source.file_id, int)
            or source.file_id < 1
            or isinstance(source.table_index, bool)
            or not isinstance(source.table_index, int)
            or source.table_index < 0
            or not isinstance(source.checksum, str)
            or len(source.checksum) != 64
            or any(character not in "0123456789abcdef" for character in source.checksum)
        ):
            raise MultiDatasetAnalyticsError(
                "invalid_source_identity",
                "A source identity or fingerprint is invalid.",
            )
        aliases.add(source.alias)
        if source.table.num_columns > MAX_ANALYTICS_COLUMNS:
            raise MultiDatasetAnalyticsError(
                "column_limit",
                f"Each input is limited to {MAX_ANALYTICS_COLUMNS} columns.",
            )
        if source.table.num_rows > MAX_ANALYTICS_ROWS:
            raise MultiDatasetAnalyticsError(
                "row_limit",
                f"Each input is limited to {MAX_ANALYTICS_ROWS} rows.",
            )
        if len(set(source.table.column_names)) != len(source.table.column_names):
            raise MultiDatasetAnalyticsError(
                "duplicate_column_names",
                f"Source {source.alias!r} has duplicate column names.",
            )
        total_rows += source.table.num_rows
        total_bytes += source.table.nbytes
    if total_rows > MAX_ANALYTICS_ROWS:
        raise MultiDatasetAnalyticsError(
            "total_input_row_limit",
            f"Selected inputs exceed the {MAX_ANALYTICS_ROWS}-row total input limit.",
        )
    if total_bytes > MAX_ANALYTICS_DECODED_BYTES:
        raise MultiDatasetAnalyticsError(
            "decoded_size_limit",
            "Selected inputs exceed the decoded-memory limit.",
        )


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _type_family(data_type: pa.DataType) -> str:
    if pa.types.is_boolean(data_type):
        return "boolean"
    if pa.types.is_integer(data_type):
        return "integer"
    if pa.types.is_floating(data_type):
        return "float"
    if pa.types.is_decimal(data_type):
        return "decimal"
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "string"
    if pa.types.is_date(data_type) or pa.types.is_timestamp(data_type):
        return "datetime"
    if pa.types.is_binary(data_type) or pa.types.is_large_binary(data_type):
        return "binary"
    return "unsupported"


def _types_compatible(left: pa.DataType, right: pa.DataType) -> bool:
    left_family = _type_family(left)
    right_family = _type_family(right)
    if left_family in {"integer", "float", "decimal"}:
        return right_family in {"integer", "float", "decimal"}
    return left_family == right_family and left_family in {
        "boolean",
        "string",
        "datetime",
        "binary",
    }


def _scalar(value: Any) -> Any:
    return value.as_py() if hasattr(value, "as_py") else value


def _hashable_key(value: Any) -> Any | None:
    value = _scalar(value)
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return tuple(_hashable_key(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable_key(item)) for key, item in value.items()))
    try:
        hash(value)
    except TypeError:
        return None
    return value


def _join_key(value: Any, data_type: pa.DataType) -> Any | None:
    value = _scalar(value)
    if value is None:
        return None
    if _type_family(data_type) in {"integer", "float", "decimal"}:
        numeric = _numeric_value(value)
        return numeric
    return _hashable_key(value)


def _column_profile(table: pa.Table, column: str) -> dict[str, Any]:
    profiled_rows = min(table.num_rows, MAX_PROFILE_ROWS)
    data_type = table.schema.field(column).type
    values = [
        _join_key(value, data_type)
        for value in table[column].slice(0, profiled_rows).to_pylist()
    ]
    present = [value for value in values if value is not None]
    unique = set(present)
    return {
        "row_count": len(values),
        "null_count": len(values) - len(present),
        "unique_count": len(unique),
        "unique_values": unique,
        "uniqueness": (len(unique) / len(present)) if present else 0.0,
        "null_rate": (len(values) - len(present)) / len(values) if values else 0.0,
        "profiled_rows": profiled_rows,
    }


def recommend_joins(sources: list[AnalyticsSource]) -> dict[str, Any]:
    _validate_sources(sources)
    profiles = {
        (source.alias, name): _column_profile(source.table, name)
        for source in sources
        for name in source.table.column_names
    }
    candidates: list[dict[str, Any]] = []
    for source_index, left in enumerate(sources):
        for right in sources[source_index + 1 :]:
            for left_name in left.table.column_names:
                left_type = left.table.schema.field(left_name).type
                for right_name in right.table.column_names:
                    right_type = right.table.schema.field(right_name).type
                    if not _types_compatible(left_type, right_type):
                        continue
                    left_profile = profiles[(left.alias, left_name)]
                    right_profile = profiles[(right.alias, right_name)]
                    left_values = left_profile["unique_values"]
                    right_values = right_profile["unique_values"]
                    overlap = len(left_values.intersection(right_values))
                    denominator = min(len(left_values), len(right_values))
                    overlap_ratio = overlap / denominator if denominator else 0.0
                    left_normalized_name = _normalized_name(left_name)
                    same_name = bool(left_normalized_name) and (
                        left_normalized_name == _normalized_name(right_name)
                    )
                    if not same_name and overlap_ratio == 0:
                        continue
                    uniqueness = min(
                        left_profile["uniqueness"],
                        right_profile["uniqueness"],
                    )
                    confidence = (
                        (0.3 if same_name else 0.0)
                        + 0.25
                        + 0.2 * uniqueness
                        + 0.1 * (1 - max(left_profile["null_rate"], right_profile["null_rate"]))
                        + 0.15 * overlap_ratio
                    )
                    candidates.append(
                        {
                            "left": {
                                "alias": left.alias,
                                "dataset_id": left.dataset_id,
                                "file_id": left.file_id,
                                "column": left_name,
                            },
                            "right": {
                                "alias": right.alias,
                                "dataset_id": right.dataset_id,
                                "file_id": right.file_id,
                                "column": right_name,
                            },
                            "confidence": round(min(confidence, 1.0), 4),
                            "confidence_is_heuristic": True,
                            "evidence": {
                                "column_names_normalized_equal": same_name,
                                "left_type": str(left_type),
                                "right_type": str(right_type),
                                "left_unique_values": len(left_values),
                                "right_unique_values": len(right_values),
                                "observed_overlap": overlap,
                                "overlap_ratio_of_smaller_key": round(overlap_ratio, 4),
                                "left_uniqueness": round(left_profile["uniqueness"], 4),
                                "right_uniqueness": round(right_profile["uniqueness"], 4),
                                "left_null_rate": round(left_profile["null_rate"], 4),
                                "right_null_rate": round(right_profile["null_rate"], 4),
                                "profiled_rows": {
                                    "left": left_profile["profiled_rows"],
                                    "right": right_profile["profiled_rows"],
                                },
                                "profile_method": "first_rows_bounded_sample",
                                "cardinality_is_sample_estimate": True,
                            },
                            "cardinality": (
                                _cardinality(
                                    left_profile["uniqueness"],
                                    right_profile["uniqueness"],
                                )
                                if left_values and right_values
                                else "undetermined"
                            ),
                            "recommendation_only": True,
                        }
                    )
    candidates.sort(
        key=lambda item: (
            -item["confidence"],
            item["left"]["alias"],
            item["left"]["column"],
            item["right"]["alias"],
            item["right"]["column"],
        )
    )
    return {
        "sources": [
            {
                "alias": source.alias,
                "dataset_id": source.dataset_id,
                "file_id": source.file_id,
                "table_index": source.table_index,
                "row_count": source.table.num_rows,
                "source_sha256": source.checksum,
                "columns": [
                    {"name": field.name, "type": str(field.type)}
                    for field in source.table.schema
                ],
            }
            for source in sources
        ],
        "recommendations": candidates[:MAX_RECOMMENDATIONS],
        "warnings": [
            "Join recommendations are evidence-based hypotheses; approve exact keys before execution."
        ],
    }


def _cardinality(left_uniqueness: float, right_uniqueness: float) -> str:
    left_unique = left_uniqueness == 1.0
    right_unique = right_uniqueness == 1.0
    if left_unique and right_unique:
        return "one_to_one"
    if left_unique:
        return "one_to_many"
    if right_unique:
        return "many_to_one"
    return "many_to_many"


def _source_frame(source: AnalyticsSource, selected_columns: list[str]) -> _Frame:
    table = source.table.select(selected_columns)
    names = [f"{source.alias}.{name}" for name in table.column_names]
    rows = list(zip(*(column.to_pylist() for column in table.columns)))
    return _Frame(
        aliases={source.alias},
        names=names,
        rows=rows,
        types={
            f"{source.alias}.{field.name}": field.type
            for field in table.schema
        },
        estimated_bytes=table.nbytes,
    )


def _qualified(key: JoinKey) -> str:
    return f"{key.source_alias}.{key.column}"


def _join_frame(
    frame: _Frame,
    source: AnalyticsSource,
    right_frame: _Frame,
    step: JoinStep,
) -> tuple[_Frame, dict[str, Any]]:
    if not isinstance(step.allow_many_to_many, bool):
        raise MultiDatasetAnalyticsError(
            "invalid_join_approval",
            "Many-to-many approval must be an explicit boolean.",
        )
    if step.join_type not in {"inner", "left"}:
        raise MultiDatasetAnalyticsError("unsupported_join_type", "Join type must be inner or left.")
    if step.right.source_alias != source.alias or source.alias in frame.aliases:
        raise MultiDatasetAnalyticsError(
            "invalid_join_order",
            "Each join must add one not-yet-joined source on the right.",
        )
    left_name = _qualified(step.left)
    right_name = _qualified(step.right)
    if step.left.source_alias not in frame.aliases:
        raise MultiDatasetAnalyticsError(
            "missing_join_key",
            "The left join key must reference a source already in the join.",
        )
    if left_name not in frame.types or step.right.column not in source.table.column_names:
        raise MultiDatasetAnalyticsError(
            "missing_join_key",
            "A selected join key does not exist in its source.",
        )
    left_type = frame.types[left_name]
    right_type = source.table.schema.field(step.right.column).type
    if not _types_compatible(left_type, right_type):
        raise MultiDatasetAnalyticsError(
            "incompatible_join_types",
            "Join keys have incompatible data types.",
        )

    left_index = frame.names.index(left_name)
    if right_frame.aliases != {source.alias}:
        raise MultiDatasetAnalyticsError("invalid_join_source", "The right-side source is invalid.")
    right_index = right_frame.names.index(right_name)
    right_rows = right_frame.rows
    left_keys = [_join_key(row[left_index], left_type) for row in frame.rows]
    right_keys = [_join_key(row[right_index], right_type) for row in right_rows]
    left_null_key_rows = left_keys.count(None)
    right_null_key_rows = right_keys.count(None)
    left_counts = Counter(key for key in left_keys if key is not None)
    right_counts = Counter(key for key in right_keys if key is not None)
    left_unique = all(count == 1 for count in left_counts.values())
    right_unique = all(count == 1 for count in right_counts.values())
    many_to_many = not left_unique and not right_unique
    if many_to_many and not step.allow_many_to_many:
        raise MultiDatasetAnalyticsError(
            "many_to_many_confirmation_required",
            "Both join keys contain duplicates. Explicitly approve a bounded many-to-many join.",
        )

    right_lookup: dict[Any, list[int]] = defaultdict(list)
    for row_index, key in enumerate(right_keys):
        if key is not None:
            right_lookup[key].append(row_index)
    matched_count = sum(
        left_counts[key] * right_counts[key]
        for key in left_counts.keys() & right_counts.keys()
    )
    projected = matched_count
    if step.join_type == "left":
        projected += sum(
            count
            for key, count in left_counts.items()
            if key not in right_counts
        )
        projected += left_keys.count(None)
    if projected > MAX_ANALYTICS_ROWS:
        raise MultiDatasetAnalyticsError(
            "row_explosion_limit",
            f"The join would produce more than {MAX_ANALYTICS_ROWS} rows.",
        )
    estimated_bytes_per_row = (
        frame.estimated_bytes / max(len(frame.rows), 1)
        + right_frame.estimated_bytes / max(len(right_rows), 1)
    )
    projected_bytes = int(projected * estimated_bytes_per_row)
    if projected_bytes > MAX_ANALYTICS_JOIN_BYTES:
        raise MultiDatasetAnalyticsError(
            "join_memory_limit",
            "The join is estimated to exceed the in-memory output limit.",
        )

    joined_rows: list[tuple[Any, ...]] = []
    unmatched = 0
    for left_row, key in zip(frame.rows, left_keys):
        matching_indices = right_lookup.get(key, []) if key is not None else []
        if matching_indices:
            joined_rows.extend(
                left_row + right_rows[right_row_index]
                for right_row_index in matching_indices
            )
        elif step.join_type == "left":
            joined_rows.append(left_row + (None,) * len(right_frame.names))
            unmatched += 1

    names = frame.names + right_frame.names
    types = {
        **frame.types,
        **right_frame.types,
    }
    return (
        _Frame(
            frame.aliases | {source.alias},
            names,
            joined_rows,
            types,
            projected_bytes,
        ),
        {
            "left_key": left_name,
            "right_key": right_name,
            "join_type": step.join_type,
            "input_rows": {"left": len(frame.rows), "right": len(right_rows)},
            "output_rows": len(joined_rows),
            "unmatched_left_rows": unmatched,
            "null_key_rows": {
                "left": left_null_key_rows,
                "right": right_null_key_rows,
            },
            "cardinality": (
                _cardinality(
                    1.0 if left_unique else 0.0,
                    1.0 if right_unique else 0.0,
                )
                if left_counts and right_counts
                else "undetermined"
            ),
            "many_to_many": many_to_many,
        },
    )


def _numeric_value(value: Any) -> Decimal | None:
    value = _scalar(value)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _json_value(value: Any) -> Any:
    value = _scalar(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        converted = float(value)
        if not math.isfinite(converted):
            raise MultiDatasetAnalyticsError(
                "numeric_output_overflow",
                "An aggregate exceeds the supported numeric output range.",
            )
        return converted
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def _aggregate(
    frame: _Frame,
    dimensions: list[JoinKey],
    metrics: list[Metric],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dimension_names = [_qualified(item) for item in dimensions]
    if len(set(dimension_names)) != len(dimension_names):
        raise MultiDatasetAnalyticsError("duplicate_dimension", "Dimensions must be unique.")
    if any(name not in frame.types for name in dimension_names):
        raise MultiDatasetAnalyticsError("missing_dimension", "A selected dimension does not exist.")
    if any(
        _type_family(frame.types[name]) == "unsupported"
        for name in dimension_names
    ):
        raise MultiDatasetAnalyticsError(
            "invalid_dimension_type",
            "Dimensions must use a supported scalar column type.",
        )
    if not metrics or len(metrics) > 16:
        raise MultiDatasetAnalyticsError(
            "invalid_metrics",
            "Select between 1 and 16 aggregate metrics.",
        )
    metric_names = [metric.name for metric in metrics]
    if any(not name or len(name) > 64 for name in metric_names) or len(set(metric_names)) != len(metric_names):
        raise MultiDatasetAnalyticsError("invalid_metric_name", "Metric names must be unique and bounded.")
    if set(metric_names).intersection(dimension_names):
        raise MultiDatasetAnalyticsError("duplicate_output_name", "Metric names cannot shadow dimensions.")

    metric_indices: list[int | None] = []
    for metric in metrics:
        if metric.operation not in {
            "count",
            "sum",
            "average",
            "minimum",
            "maximum",
            "distinct_count",
        }:
            raise MultiDatasetAnalyticsError("unsupported_aggregation", "The aggregation operation is unsupported.")
        if metric.column is None:
            if metric.operation != "count":
                raise MultiDatasetAnalyticsError(
                    "missing_metric_column",
                    "Only count may omit a metric column.",
                )
            metric_indices.append(None)
            continue
        name = _qualified(metric.column)
        if name not in frame.types:
            raise MultiDatasetAnalyticsError("missing_metric_column", "A metric column does not exist.")
        data_type = frame.types[name]
        if metric.operation in {"sum", "average"} and _type_family(data_type) not in {
            "integer",
            "float",
            "decimal",
        }:
            raise MultiDatasetAnalyticsError(
                "invalid_metric_type",
                f"{metric.operation} requires a numeric column.",
            )
        if metric.operation in {"minimum", "maximum"} and _type_family(data_type) in {
            "unsupported",
            "binary",
        }:
            raise MultiDatasetAnalyticsError(
                "invalid_metric_type",
                f"{metric.operation} does not support this column type.",
            )
        metric_indices.append(frame.names.index(name))

    dimension_indices = [frame.names.index(name) for name in dimension_names]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    group_row_counts: dict[tuple[Any, ...], int] = {}
    distinct_values_stored = 0
    if not frame.rows and not dimension_indices:
        groups[()] = [
            {"count": 0, "valid_count": 0, "values": []}
            for _metric in metrics
        ]
        group_row_counts[()] = 0
    for row in frame.rows:
        dimension_values = tuple(_hashable_key(row[index]) for index in dimension_indices)
        if dimension_values not in groups:
            groups[dimension_values] = [
                {"count": 0, "valid_count": 0, "values": []}
                for _metric in metrics
            ]
            group_row_counts[dimension_values] = 0
        group_row_counts[dimension_values] += 1
        group = groups[dimension_values]
        for metric_index, (metric, column_index) in enumerate(zip(metrics, metric_indices)):
            state = group[metric_index]
            if metric.operation == "count":
                if column_index is None or row[column_index] is not None:
                    state["count"] += 1
                    state["valid_count"] += 1
                continue
            value = row[column_index] if column_index is not None else None
            if value is None:
                continue
            if metric.operation in {"sum", "average"}:
                numeric_value = _numeric_value(value)
                if numeric_value is None:
                    continue
                state["count"] += 1
                state["valid_count"] += 1
                with localcontext() as context:
                    context.prec = 128
                    state["sum"] = state.get("sum", Decimal(0)) + numeric_value
                continue
            if _type_family(frame.types[_qualified(metric.column)]) in {
                "integer",
                "float",
                "decimal",
            } and _numeric_value(value) is None:
                continue
            state["valid_count"] += 1
            if metric.operation == "distinct_count":
                distinct_value = _hashable_key(value)
                distinct = state.setdefault("distinct", set())
                if distinct_value is not None and distinct_value not in distinct:
                    if distinct_values_stored >= MAX_ANALYTICS_DISTINCT_VALUES:
                        raise MultiDatasetAnalyticsError(
                            "distinct_value_limit",
                            "Grouped distinct counts exceed the bounded value limit.",
                        )
                    distinct.add(distinct_value)
                    distinct_values_stored += 1
            elif metric.operation == "minimum":
                current = state.get("value")
                state["value"] = value if current is None or value < current else current
            elif metric.operation == "maximum":
                current = state.get("value")
                state["value"] = value if current is None or value > current else current

        if len(groups) > MAX_ANALYTICS_GROUPS:
            raise MultiDatasetAnalyticsError(
                "group_limit",
                f"Grouped analytics are limited to {MAX_ANALYTICS_GROUPS} groups.",
            )

    output = []
    evidence = []
    for dimension_values in sorted(
        groups,
        key=lambda values: repr(tuple(_json_value(value) for value in values)),
    ):
        dimension_output = {
            name: _json_value(value)
            for name, value in zip(dimension_names, dimension_values)
        }
        row_output = dict(dimension_output)
        metric_evidence = []
        for metric, state in zip(metrics, groups[dimension_values]):
            if metric.operation == "count":
                value = state["count"]
            elif metric.operation == "sum":
                value = _json_value(state.get("sum", Decimal(0)))
            elif metric.operation == "average":
                if state["count"]:
                    with localcontext() as context:
                        context.prec = 128
                        value = _json_value(state["sum"] / state["count"])
                else:
                    value = None
            elif metric.operation == "distinct_count":
                value = len(state.get("distinct", set()))
            else:
                value = _json_value(state.get("value"))
            row_output[metric.name] = value
            metric_evidence.append(
                {
                    "name": metric.name,
                    "value": value,
                    "valid_count": state["valid_count"],
                    "missing_count": (
                        group_row_counts[dimension_values] - state["valid_count"]
                    ),
                }
            )
        output.append(row_output)
        evidence.append(
            {
                **dimension_output,
                "group_row_count": group_row_counts[dimension_values],
                "metrics": metric_evidence,
            }
        )
    return output, evidence


def execute_join_aggregation(
    sources: list[AnalyticsSource],
    joins: list[JoinStep],
    dimensions: list[JoinKey],
    metrics: list[Metric],
) -> dict[str, Any]:
    _validate_sources(sources)
    if not isinstance(joins, list) or len(joins) != len(sources) - 1:
        raise MultiDatasetAnalyticsError(
            "invalid_join_plan",
            "Provide one join for each source added after the first.",
        )
    source_by_alias = {source.alias: source for source in sources}
    joined_aliases = {sources[0].alias}
    for expected_source, step in zip(sources[1:], joins):
        if step.right.source_alias != expected_source.alias:
            raise MultiDatasetAnalyticsError(
                "invalid_join_order",
                "Join steps must follow the selected source order.",
            )
        left_source = source_by_alias.get(step.left.source_alias)
        if (
            step.left.source_alias not in joined_aliases
            or left_source is None
            or step.left.column not in left_source.table.column_names
            or step.right.column not in expected_source.table.column_names
        ):
            raise MultiDatasetAnalyticsError(
                "missing_join_key",
                "A selected join key does not exist in its source.",
            )
        joined_aliases.add(expected_source.alias)

    required_columns = {source.alias: set() for source in sources}
    for step in joins:
        required_columns.setdefault(step.left.source_alias, set()).add(step.left.column)
        required_columns.setdefault(step.right.source_alias, set()).add(step.right.column)
    for dimension in dimensions:
        required_columns.setdefault(dimension.source_alias, set()).add(dimension.column)
    for metric in metrics:
        if metric.column is not None:
            required_columns.setdefault(metric.column.source_alias, set()).add(
                metric.column.column
            )
    selected_sources = {}
    for source in sources:
        if source.alias not in required_columns:
            raise MultiDatasetAnalyticsError("unknown_source_alias", "A selected source alias does not exist.")
        selected = [
            name for name in source.table.column_names
            if name in required_columns[source.alias]
        ]
        if not selected:
            raise MultiDatasetAnalyticsError("empty_source_projection", "Each source must contribute a selected key or metric.")
        if len(selected) != len(required_columns[source.alias]):
            raise MultiDatasetAnalyticsError("missing_source_column", "A selected source column does not exist.")
        selected_sources[source.alias] = AnalyticsSource(
            alias=source.alias,
            dataset_id=source.dataset_id,
            file_id=source.file_id,
            table_index=source.table_index,
            table=source.table.select(selected),
            checksum=source.checksum,
        )
    frame = _source_frame(
        selected_sources[sources[0].alias],
        selected_sources[sources[0].alias].table.column_names,
    )
    join_evidence = []
    for source_alias, step in zip(
        [source.alias for source in sources[1:]],
        joins,
    ):
        if step.right.source_alias != source_alias:
            raise MultiDatasetAnalyticsError(
                "invalid_join_order",
                "Join steps must follow the selected source order.",
            )
        frame, evidence = _join_frame(
            frame,
            source_by_alias[source_alias],
            _source_frame(
                selected_sources[source_alias],
                selected_sources[source_alias].table.column_names,
            ),
            step,
        )
        join_evidence.append(evidence)
    results, result_evidence = _aggregate(frame, dimensions, metrics)
    if len(results) > MAX_ANALYTICS_GROUPS:
        raise MultiDatasetAnalyticsError("group_limit", "Grouped analytics exceeded the group limit.")
    warnings = []
    for item in join_evidence:
        if item["many_to_many"]:
            warnings.append("An explicitly approved many-to-many join was executed.")
        if item["unmatched_left_rows"]:
            warnings.append(
                f"{item['unmatched_left_rows']} left-side rows had no matching key."
            )
        if item["null_key_rows"]["left"] or item["null_key_rows"]["right"]:
            warnings.append(
                "Null join keys did not match "
                f"(left: {item['null_key_rows']['left']}, "
                f"right: {item['null_key_rows']['right']})."
            )
    result = {
        "dimensions": [_qualified(item) for item in dimensions],
        "metrics": [
            {
                "name": item.name,
                "operation": item.operation,
                "column": _qualified(item.column) if item.column else None,
            }
            for item in metrics
        ],
        "input_sources": [
            {
                "alias": source.alias,
                "dataset_id": source.dataset_id,
                "file_id": source.file_id,
                "table_index": source.table_index,
                "source_sha256": source.checksum,
                "row_count": source.table.num_rows,
            }
            for source in sources
        ],
        "join_evidence": join_evidence,
        "joined_row_count": len(frame.rows),
        "group_count": len(results),
        "results": results,
        "result_evidence": result_evidence,
        "warnings": warnings,
    }
    identity = json.dumps(
        {
            "sources": result["input_sources"],
            "joins": [
                {
                    "left_key": item["left_key"],
                    "right_key": item["right_key"],
                    "join_type": item["join_type"],
                    "many_to_many": item["many_to_many"],
                }
                for item in join_evidence
            ],
            "dimensions": result["dimensions"],
            "metrics": result["metrics"],
        },
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    result["execution_fingerprint"] = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()
    return result
