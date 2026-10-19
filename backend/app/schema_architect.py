from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc


SCHEMA_ARCHITECT_VERSION = "1"
MAX_TABLES = 20
MAX_COLUMNS_PER_TABLE = 200
MAX_TOTAL_ROWS = 1_000_000
MAX_TOTAL_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_RETAINED_VALUES = 100_000
MAX_RETAINED_VALUES_PER_COLUMN = 10_000
MAX_RETAINED_VALUE_BYTES = 32 * 1024 * 1024
MAX_VALUE_TRACK_BYTES = 16_384
MAX_RELATIONSHIP_CANDIDATES = 500
MAX_RELATIONSHIP_COMPARISONS = 10_000
MAX_QUALITY_WARNINGS_PER_TABLE = 50
_IDENTIFIER_TOKENS = {"id", "key", "code", "number", "num", "ref"}


class SchemaAnalysisError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SchemaTableInput:
    identity: dict[str, Any]
    schema: pa.Schema
    iter_batches: Callable[[], Iterator[pa.RecordBatch]]
    source_profile: dict[str, Any] | None = None
    normalization_warnings: tuple[str, ...] = ()
    quality_warnings: tuple[dict[str, Any], ...] = ()


@dataclass
class _ColumnStats:
    distinct_values: dict[bytes, int]
    null_count: int = 0
    non_null_count: int = 0
    tracking_complete: bool = True


def _canonical_value(value: Any) -> bytes:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            encoded = "nan"
        elif isinstance(value, float) and math.isinf(value):
            encoded = "inf" if value > 0 else "-inf"
        else:
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                number = Decimal(value)
            encoded = "0" if number == 0 else str(number.normalize())
        text = f"numeric:{encoded}"
    elif isinstance(value, (bytes, bytearray, memoryview)):
        text = "bytes:" + bytes(value).hex()
    else:
        text = (
            type(value).__name__
            + ":"
            + json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
                allow_nan=False,
            )
        )
    return text.encode("utf-8")


def _type_family(data_type: pa.DataType) -> str:
    if pa.types.is_dictionary(data_type):
        return _type_family(data_type.value_type)
    if pa.types.is_integer(data_type) or pa.types.is_floating(data_type) or pa.types.is_decimal(data_type):
        return "numeric"
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "string"
    if pa.types.is_binary(data_type) or pa.types.is_large_binary(data_type) or pa.types.is_fixed_size_binary(data_type):
        return "binary"
    if pa.types.is_boolean(data_type):
        return "boolean"
    if pa.types.is_date(data_type) or pa.types.is_timestamp(data_type) or pa.types.is_time(data_type):
        return "temporal"
    return "unsupported"


def _compatible_types(left: pa.DataType, right: pa.DataType) -> bool:
    left_family = _type_family(left)
    if left_family == "unsupported" or left_family != _type_family(right):
        return False
    if left_family == "temporal":
        return left == right
    return True


def _column_name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.casefold())


def _column_name_similarity(left: str, right: str) -> float:
    left_key = _column_name_key(left)
    right_key = _column_name_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    left_base = re.sub(r"(?:id|key|code)$", "", left_key)
    right_base = re.sub(r"(?:id|key|code)$", "", right_key)
    if left_base and left_base == right_base:
        return 0.85
    return SequenceMatcher(None, left_key, right_key, autojunk=False).ratio()


def _quality_warning_summary(warning: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in ("rule_id", "title", "severity", "classification", "table_index"):
        value = warning.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            result[key] = value[:256] if isinstance(value, str) else value
    if warning.get("scope") is not None:
        scope = warning["scope"]
        if isinstance(scope, dict):
            safe_scope = {
                key: value
                for key, value in scope.items()
                if key in {"table_index", "column", "column_name"}
                and isinstance(value, (str, int))
                and not isinstance(value, bool)
            }
            if safe_scope:
                result["scope"] = safe_scope
    return result


def _profile_columns(profile: dict[str, Any] | None, table_name: str | None) -> dict[tuple[str, int], dict]:
    if not isinstance(profile, dict):
        return {}
    tables = profile.get("tables")
    if not isinstance(tables, list):
        return {}
    for table in tables:
        if not isinstance(table, dict) or table.get("name") != table_name:
            continue
        columns = table.get("columns")
        if not isinstance(columns, list):
            return {}
        return {
            (column["name"], column["position"]): column
            for column in columns
            if isinstance(column, dict)
            and isinstance(column.get("name"), str)
            and isinstance(column.get("position"), int)
            and not isinstance(column.get("position"), bool)
        }
    return {}


def _trackable_type(data_type: pa.DataType) -> bool:
    return _type_family(data_type) != "unsupported"


def _collect_table(
    table: SchemaTableInput,
    retained_value_budget: list[int],
    row_budget: list[int],
) -> dict[str, Any]:
    if len(table.schema) > MAX_COLUMNS_PER_TABLE:
        raise SchemaAnalysisError(
            "resource_limit",
            f"A table exceeds the {MAX_COLUMNS_PER_TABLE}-column schema-analysis limit.",
        )

    stats = [_ColumnStats({}) for _ in table.schema]
    row_count = 0
    try:
        for batch in table.iter_batches():
            if batch.num_columns != len(table.schema):
                raise SchemaAnalysisError(
                    "invalid_artifact",
                    "A normalized table batch does not match its declared schema.",
                )
            row_count += batch.num_rows
            row_budget[0] += batch.num_rows
            if row_budget[0] > MAX_TOTAL_ROWS:
                raise SchemaAnalysisError(
                    "resource_limit",
                    f"Schema analysis is limited to {MAX_TOTAL_ROWS} rows across selected tables.",
                )

            for column_index, field in enumerate(table.schema):
                column = batch.column(column_index)
                column_stats = stats[column_index]
                column_stats.null_count += column.null_count
                column_stats.non_null_count += len(column) - column.null_count
                if not column_stats.tracking_complete:
                    continue
                if not _trackable_type(field.type):
                    if column.null_count < len(column):
                        column_stats.tracking_complete = False
                    continue

                values_column = (
                    column.dictionary_decode()
                    if pa.types.is_dictionary(field.type)
                    else column
                )
                lengths = None
                if (
                    pa.types.is_string(field.type)
                    or pa.types.is_large_string(field.type)
                    or pa.types.is_binary(field.type)
                    or pa.types.is_large_binary(field.type)
                ):
                    lengths = pc.binary_length(values_column)

                for value_index in range(len(values_column)):
                    scalar = values_column[value_index]
                    if not scalar.is_valid:
                        continue
                    if lengths is not None:
                        value_size = lengths[value_index].as_py()
                        if value_size > MAX_VALUE_TRACK_BYTES:
                            column_stats.tracking_complete = False
                            continue

                    value = scalar.as_py()
                    value_key = _canonical_value(value)
                    if len(value_key) > MAX_VALUE_TRACK_BYTES:
                        column_stats.tracking_complete = False
                        continue
                    if value_key in column_stats.distinct_values:
                        column_stats.distinct_values[value_key] += 1
                        continue
                    if (
                        retained_value_budget[0] >= MAX_RETAINED_VALUES
                        or retained_value_budget[1] + len(value_key)
                        > MAX_RETAINED_VALUE_BYTES
                        or len(column_stats.distinct_values)
                        >= MAX_RETAINED_VALUES_PER_COLUMN
                    ):
                        column_stats.tracking_complete = False
                        continue
                    column_stats.distinct_values[value_key] = 1
                    retained_value_budget[0] += 1
                    retained_value_budget[1] += len(value_key)
    except SchemaAnalysisError:
        raise
    except (OSError, ValueError, TypeError, OverflowError, pa.ArrowException):
        raise SchemaAnalysisError(
            "invalid_artifact",
            "A normalized table could not be scanned safely.",
        ) from None

    profile_columns = _profile_columns(
        table.source_profile,
        table.identity.get("table_name"),
    )
    duplicate_names = {
        name for name in table.schema.names if table.schema.names.count(name) > 1
    }
    columns = []
    for position, (field, column_stats) in enumerate(zip(table.schema, stats)):
        distinct_count = (
            len(column_stats.distinct_values)
            if column_stats.tracking_complete
            else None
        )
        duplicate_count = (
            sum(count - 1 for count in column_stats.distinct_values.values())
            if column_stats.tracking_complete
            else None
        )
        if not column_stats.tracking_complete:
            key_status = "not_evaluated"
        elif (
            row_count > 0
            and column_stats.null_count == 0
            and distinct_count == row_count
        ):
            key_status = "candidate"
        else:
            key_status = "not_candidate"
        unique_ratio = (
            distinct_count / column_stats.non_null_count
            if distinct_count is not None and column_stats.non_null_count
            else None
        )
        profile_column = profile_columns.get((field.name, position), {})
        physical_types = profile_column.get("physical_types")
        if not isinstance(physical_types, list):
            physical_types = []
        name_tokens = set(re.findall(r"[a-z0-9]+", field.name.casefold()))
        identifier_like = (
            bool(name_tokens & _IDENTIFIER_TOKENS)
            or unique_ratio is not None and unique_ratio >= 0.8
        )
        categorical = (
            distinct_count is not None
            and distinct_count <= 20
            and _type_family(field.type) in {"string", "boolean"}
        )
        warnings = []
        if column_stats.non_null_count == 0:
            warnings.append("all_values_null")
        if _type_family(field.type) == "unsupported":
            warnings.append("unsupported_type")
        if len(physical_types) > 1:
            warnings.append("source_type_inconsistency")
        if field.name in duplicate_names:
            warnings.append("duplicate_column_name")

        identifier_name_candidate = bool(name_tokens & _IDENTIFIER_TOKENS)
        role_candidates = []
        if identifier_like:
            role_candidates.append("identifier")
        if _type_family(field.type) == "temporal":
            role_candidates.append("date_or_time_attribute")
        if categorical or (
            _type_family(field.type) == "string"
            and not identifier_like
        ):
            role_candidates.append("descriptive_attribute")
        if (
            _type_family(field.type) == "numeric"
            and not identifier_name_candidate
        ):
            role_candidates.append("numeric_measure")
        columns.append(
            {
                "name": field.name,
                "position": position,
                "normalized_type": str(field.type),
                "type_family": _type_family(field.type),
                "source_physical_types": physical_types,
                "row_count": row_count,
                "null_count": column_stats.null_count,
                "null_rate": (
                    column_stats.null_count / row_count if row_count else None
                ),
                "non_null_count": column_stats.non_null_count,
                "distinct_count": distinct_count,
                "distinct_count_exact": column_stats.tracking_complete,
                "unique_non_null_ratio": unique_ratio,
                "duplicate_non_null_rows": duplicate_count,
                "candidate_key_status": key_status,
                "identifier_like": identifier_like,
                "identifier_name_candidate": identifier_name_candidate,
                "categorical_candidate": categorical,
                "role_candidates": role_candidates,
                "semantic_role_status": (
                    "heuristic_candidates" if role_candidates else "uncertain"
                ),
                "warnings": warnings,
                "_distinct_values": (
                    set(column_stats.distinct_values)
                    if column_stats.tracking_complete
                    else None
                ),
                "_value_counts": (
                    column_stats.distinct_values
                    if column_stats.tracking_complete
                    else None
                ),
            }
        )

    schema_warnings = list(
        table.normalization_warnings[:MAX_QUALITY_WARNINGS_PER_TABLE]
    )
    if any(
        table.schema.names.count(name) > 1
        for name in set(table.schema.names)
    ):
        schema_warnings.append("duplicate_column_names")
    if row_count == 0:
        schema_warnings.append("empty_table")
    quality_warnings = [
        _quality_warning_summary(item)
        for item in table.quality_warnings[:MAX_QUALITY_WARNINGS_PER_TABLE]
        if isinstance(item, dict)
    ]
    visible_columns = [
        {key: value for key, value in column.items() if not key.startswith("_")}
        for column in columns
    ]
    result = {
        "identity": table.identity,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": visible_columns,
        "schema_warnings": sorted(set(schema_warnings)),
        "quality_warnings": quality_warnings,
        "_columns": columns,
        "_table": table,
    }
    return result


def _candidate_pairs(
    tables: list[dict],
) -> tuple[list[tuple[int, int, int, int, float, bool]], bool]:
    candidates = []
    comparisons = 0
    complete = True
    for left_table_index in range(len(tables)):
        left_columns = tables[left_table_index]["_columns"]
        for right_table_index in range(left_table_index + 1, len(tables)):
            right_columns = tables[right_table_index]["_columns"]
            right_names: dict[str, list[int]] = {}
            for right_index, right in enumerate(right_columns):
                right_key = _column_name_key(right["name"])
                right_names.setdefault(right_key, []).append(right_index)
                for suffix in ("id", "key", "code"):
                    if right_key.endswith(suffix) and len(right_key) > len(suffix):
                        right_names.setdefault(
                            right_key[: -len(suffix)], []
                        ).append(right_index)
            for left_index, left in enumerate(left_columns):
                left_key = _column_name_key(left["name"])
                candidate_names = {left_key}
                for suffix in ("id", "key", "code"):
                    if left_key.endswith(suffix) and len(left_key) > len(suffix):
                        candidate_names.add(left_key[: -len(suffix)])
                matching_right_indices = sorted(
                    {
                        right_index
                        for candidate_name in candidate_names
                        for right_index in right_names.get(candidate_name, ())
                    }
                )
                for right_index in matching_right_indices:
                    comparisons += 1
                    if comparisons > MAX_RELATIONSHIP_COMPARISONS:
                        complete = False
                        return candidates, complete
                    right = right_columns[right_index]
                    type_compatible = _compatible_types(
                        tables[left_table_index]["_table"].schema.field(left_index).type,
                        tables[right_table_index]["_table"].schema.field(right_index).type,
                    )
                    similarity = _column_name_similarity(left["name"], right["name"])
                    if similarity < 0.8:
                        continue
                    if len(candidates) >= MAX_RELATIONSHIP_CANDIDATES:
                        complete = False
                        return candidates, complete
                    candidates.append(
                        (
                            left_table_index,
                            left_index,
                            right_table_index,
                            right_index,
                            similarity,
                            type_compatible,
                        )
                    )
    return candidates, complete


def _relationship(
    tables: list[dict],
    candidate: tuple[int, int, int, int, float, bool],
) -> dict[str, Any]:
    (
        left_index,
        left_column_index,
        right_index,
        right_column_index,
        similarity,
        type_compatible,
    ) = candidate
    left_table = tables[left_index]
    right_table = tables[right_index]
    left = left_table["_columns"][left_column_index]
    right = right_table["_columns"][right_column_index]
    left_values = left["_distinct_values"]
    right_values = right["_distinct_values"]
    overlap = len(left_values & right_values) if left_values is not None and right_values is not None else None
    left_coverage = (
        overlap / left["distinct_count"]
        if overlap is not None and left["distinct_count"]
        else 0.0 if overlap == 0 else None
    )
    right_coverage = (
        overlap / right["distinct_count"]
        if overlap is not None and right["distinct_count"]
        else 0.0 if overlap == 0 else None
    )
    left_key = left["candidate_key_status"] == "candidate"
    right_key = right["candidate_key_status"] == "candidate"
    if left_key and not right_key:
        source_table, source_column = right_table, right
        target_table, target_column = left_table, left
        source_coverage = right_coverage
        target_key = left_key
        source_key = right_key
    else:
        source_table, source_column = left_table, left
        target_table, target_column = right_table, right
        source_coverage = left_coverage
        target_key = right_key
        source_key = left_key
    if source_key and target_key:
        cardinality = "one_to_one"
    elif source_key:
        cardinality = "one_to_many"
    elif target_key:
        cardinality = "many_to_one"
    else:
        cardinality = "many_to_many"

    if not type_compatible or overlap == 0:
        status = "rejected"
    elif (
        target_key
        and source_coverage is not None
        and source_coverage >= 0.9
        and similarity >= 0.8
    ):
        status = "inferred"
    else:
        status = "ambiguous"

    score = 0.3 * similarity
    score += 0.2 if type_compatible else 0.0
    score += 0.2 if target_key else 0.0
    score += 0.15 * (source_coverage or 0.0) if source_coverage is not None else 0.0
    score += 0.05 if overlap is not None and overlap > 0 else 0.0
    if source_column["null_rate"] is not None:
        score += 0.1 * (1 - source_column["null_rate"])
    confidence = "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"
    risks = []
    if source_coverage is None:
        risks.append("Value overlap was not evaluated exactly within the retained-value budget.")
    elif source_coverage < 1 or (
        source_column["null_rate"] is not None
        and source_column["null_rate"] > 0
    ):
        risks.append("A left join may lose unmatched source rows in an inner join.")
    if cardinality in {"one_to_many", "many_to_many"}:
        risks.append("Joining in this direction may multiply source rows.")
    if not target_key:
        risks.append("The target column is not an observed non-null unique candidate key.")
    if overlap is not None and overlap == 0:
        risks.append("No shared non-null values were observed.")
    if not type_compatible:
        risks.append("The normalized data types are incompatible for a safe equality join.")
    quality_warnings = []
    for table, column in (
        (source_table, source_column),
        (target_table, target_column),
    ):
        for warning in table["quality_warnings"]:
            scope = warning.get("scope")
            scoped_column = (
                scope.get("column") or scope.get("column_name")
                if isinstance(scope, dict)
                else None
            )
            if scoped_column is None or scoped_column == column["name"]:
                quality_warnings.append(warning)
    if quality_warnings:
        risks.append("Relevant stored quality findings apply to one or both relationship columns.")

    evidence = {
        "column_name_similarity": round(similarity, 4),
        "type_compatibility": "compatible" if type_compatible else "incompatible",
        "source_normalized_type": str(source_table["_table"].schema.field(source_column["position"]).type),
        "target_normalized_type": str(target_table["_table"].schema.field(target_column["position"]).type),
        "left_distinct_count": left["distinct_count"],
        "right_distinct_count": right["distinct_count"],
        "left_duplicate_non_null_rows": left["duplicate_non_null_rows"],
        "right_duplicate_non_null_rows": right["duplicate_non_null_rows"],
        "left_distinct_count_exact": left["distinct_count_exact"],
        "right_distinct_count_exact": right["distinct_count_exact"],
        "non_null_overlap_count": overlap,
        "left_referential_coverage": (
            round(left_coverage, 6) if left_coverage is not None else None
        ),
        "right_referential_coverage": (
            round(right_coverage, 6) if right_coverage is not None else None
        ),
        "source_candidate_key": source_key,
        "target_candidate_key": target_key,
        "source_null_rate": source_column["null_rate"],
        "target_null_rate": target_column["null_rate"],
        "source_referential_coverage": (
            round(source_coverage, 6) if source_coverage is not None else None
        ),
        "method": "Exact set intersection over retained normalized values; unavailable when value tracking exceeded its bound.",
    }
    return {
        "source": {
            "table": source_table["identity"],
            "column": source_column["name"],
            "position": source_column["position"],
        },
        "target": {
            "table": target_table["identity"],
            "column": target_column["name"],
            "position": target_column["position"],
        },
        "cardinality_hypothesis": cardinality,
        "status": status,
        "confidence": confidence,
        "confidence_score": round(score, 4),
        "evidence": evidence,
        "quality_warnings": quality_warnings,
        "risks": risks,
        "unresolved_questions": [
            "Does the proposed relationship represent the intended business concept?",
            "Are the observed key values stable and governed across source systems?",
        ],
        "evidence_origin": "observed_normalized_values",
        "_source_table_index": (
            right_index if source_table is right_table else left_index
        ),
        "_target_table_index": (
            right_index if target_table is right_table else left_index
        ),
    }


def _model_recommendations(tables: list[dict], relationships: list[dict]) -> list[dict]:
    recommendations = []
    dimensions = {
        index
        for index, table in enumerate(tables)
        if any(
            column["candidate_key_status"] == "candidate"
            for column in table["_columns"]
        )
    }
    for fact_index, table in enumerate(tables):
        fact_edges = []
        for edge in relationships:
            if edge["status"] != "inferred":
                continue
            source_index = edge["_source_table_index"]
            target_index = edge["_target_table_index"]
            source_is_fact = (
                source_index == fact_index
                and target_index in dimensions
                and edge["cardinality_hypothesis"] == "many_to_one"
            )
            target_is_fact = (
                target_index == fact_index
                and source_index in dimensions
                and edge["cardinality_hypothesis"] == "one_to_many"
            )
            if source_is_fact or target_is_fact:
                fact_edges.append(edge)
        numeric_columns = [
            column["name"]
            for column in table["_columns"]
            if (
                column["type_family"] == "numeric"
                and not column["identifier_name_candidate"]
            )
        ]
        target_dimensions = sorted(
            {
                edge["_target_table_index"]
                if edge["_source_table_index"] == fact_index
                else edge["_source_table_index"]
                for edge in fact_edges
            }
        )
        if len(target_dimensions) >= 2 and numeric_columns:
            recommendations.append(
                {
                    "model_type": "star",
                    "status": "conditional",
                    "fact_table_candidate": table["identity"],
                    "dimension_table_candidates": [
                        tables[index]["identity"] for index in target_dimensions
                    ],
                    "classification_evidence": [
                        {
                            "table": table["identity"],
                            "candidate_key_columns": [
                                column["name"]
                                for column in table["_columns"]
                                if column["candidate_key_status"] == "candidate"
                            ],
                            "relationship_evidence": [
                                edge["evidence"]
                                for edge in fact_edges
                                if index == fact_index
                                or edge["_source_table_index"] == index
                                or edge["_target_table_index"] == index
                            ],
                        }
                        for index in [fact_index, *target_dimensions]
                        for table in [tables[index]]
                    ],
                    "candidate_numeric_measures": numeric_columns,
                    "measure_semantics": "Numeric storage types are evidence only; business measure meaning is unconfirmed.",
                    "grain": {
                        "status": "unconfirmed",
                        "description": None,
                        "evidence": [
                            "The table has observed many-to-one candidate relationships to at least two key-candidate tables."
                        ],
                    },
                    "recommended_join_paths": [
                        {
                            "from": (
                                edge["source"]
                                if edge["_source_table_index"] == fact_index
                                else edge["target"]
                            ),
                            "to": (
                                edge["target"]
                                if edge["_source_table_index"] == fact_index
                                else edge["source"]
                            ),
                            "relationship_status": edge["status"],
                        }
                        for edge in fact_edges
                    ],
                    "surrogate_key_guidance": "Consider a surrogate key only after confirming the business key and row grain; no key is created.",
                    "benefits": ["Simple analytical joins from a central table to candidate dimensions."],
                    "costs": ["Requires explicit grain, measure, and dimension-semantics decisions."],
                    "risks": [
                        "The central/fact role is structural rather than a verified business classification.",
                        "Candidate numeric columns are not confirmed measures.",
                    ],
                    "required_clarifications": [
                        "What does one row in the candidate fact table represent?",
                        "Which numeric columns are additive measures and what do they mean?",
                        "Do the proposed dimensions have stable business keys?",
                    ],
                }
            )

    if len(recommendations) < 5:
        for left_index, left in enumerate(tables):
            for right_index in range(left_index + 1, len(tables)):
                right = tables[right_index]
                if len(recommendations) >= 5:
                    break
                between = [
                    edge
                    for edge in relationships
                    if edge["status"] == "inferred"
                    and (
                        edge["_source_table_index"],
                        edge["_target_table_index"],
                    )
                    in {
                        (left_index, right_index),
                        (right_index, left_index),
                    }
                ]
                if not between:
                    continue
                if left_index in dimensions and right_index in dimensions:
                    recommendations.append(
                        {
                            "model_type": "snowflake",
                            "status": "conditional",
                            "fact_table_candidate": None,
                            "dimension_table_candidates": [
                                left["identity"],
                                right["identity"],
                            ],
                            "classification_evidence": [
                                {
                                    "table": left["identity"],
                                    "candidate_key_columns": [
                                        column["name"]
                                        for column in left["_columns"]
                                        if column["candidate_key_status"] == "candidate"
                                    ],
                                },
                                {
                                    "table": right["identity"],
                                    "candidate_key_columns": [
                                        column["name"]
                                        for column in right["_columns"]
                                        if column["candidate_key_status"] == "candidate"
                                    ],
                                },
                            ],
                            "candidate_numeric_measures": [],
                            "measure_semantics": "No fact or measure semantics are inferred from names or types alone.",
                            "grain": {
                                "status": "unconfirmed",
                                "description": None,
                                "evidence": [
                                    "A candidate-key relationship connects two dimension-like tables."
                                ],
                            },
                            "recommended_join_paths": [
                                {
                                    "from": edge["source"],
                                    "to": edge["target"],
                                    "relationship_status": edge["status"],
                                }
                                for edge in between
                            ],
                            "surrogate_key_guidance": "Confirm business keys before considering surrogate keys; no key is created.",
                            "benefits": ["Can represent observed hierarchical reference structure."],
                            "costs": ["Adds joins and depends on confirmed business hierarchy."],
                            "risks": ["The hierarchy and dimension semantics remain unverified."],
                            "required_clarifications": [
                                "Does this relationship represent a real dimension hierarchy?",
                                "What is the intended fact table and row grain?",
                            ],
                        }
                    )
    star_recommendations = [
        recommendation
        for recommendation in recommendations
        if recommendation["model_type"] == "star"
    ]
    for star in star_recommendations:
        fact_index = next(
            index
            for index, table in enumerate(tables)
            if table["identity"] == star["fact_table_candidate"]
        )
        dimension_indexes = {
            index
            for index, table in enumerate(tables)
            if table["identity"] in star["dimension_table_candidates"]
        }
        hierarchy_edges = [
            edge
            for edge in relationships
            if edge["status"] == "inferred"
            and edge["_source_table_index"] in dimension_indexes
            and edge["_target_table_index"] in dimension_indexes
        ]
        if hierarchy_edges:
            recommendations.append(
                {
                    "model_type": "hybrid",
                    "status": "conditional",
                    "fact_table_candidate": star["fact_table_candidate"],
                    "dimension_table_candidates": star[
                        "dimension_table_candidates"
                    ],
                    "relationship_evidence": {
                        "fact_to_dimension": star["recommended_join_paths"],
                        "dimension_hierarchies": [
                            {
                                "source": edge["source"],
                                "target": edge["target"],
                                "evidence": edge["evidence"],
                            }
                            for edge in hierarchy_edges
                        ],
                    },
                    "candidate_numeric_measures": star[
                        "candidate_numeric_measures"
                    ],
                    "measure_semantics": star["measure_semantics"],
                    "grain": star["grain"],
                    "risks": [
                        "The fact role, dimension roles, hierarchy, and measure semantics remain unverified.",
                        "Snowflake joins add complexity and may affect query behavior.",
                    ],
                    "required_clarifications": [
                        "Does the dimension relationship represent a governed hierarchy?",
                        "What does one row in the candidate fact table represent?",
                        "Which numeric columns are business measures?",
                    ],
                }
            )
    if not recommendations:
        recommendations.append(
            {
                "model_type": "normalized",
                "status": "conservative",
                "fact_table_candidate": None,
                "dimension_table_candidates": [],
                "table_candidates": [
                    table["identity"] for table in tables
                ],
                "relationship_evidence": {
                    "inferred_relationship_count": sum(
                        edge["status"] == "inferred"
                        for edge in relationships
                    ),
                    "ambiguous_relationship_count": sum(
                        edge["status"] == "ambiguous"
                        for edge in relationships
                    ),
                    "rejected_relationship_count": sum(
                        edge["status"] == "rejected"
                        for edge in relationships
                    ),
                },
                "rationale": (
                    "Available evidence does not support a dimensional "
                    "restructure; preserve the selected tables pending "
                    "business confirmation."
                ),
                "risks": [
                    "This is a conservative preservation recommendation, not a verified assessment of source normalization."
                ],
                "required_clarifications": [
                    "Which candidate relationships represent the intended business concepts?",
                    "What analytical grain and measures should a dimensional model use?",
                ],
            }
        )
    return recommendations


def analyze_schema(tables: list[SchemaTableInput]) -> dict[str, Any]:
    if not isinstance(tables, list) or not tables:
        raise SchemaAnalysisError(
            "invalid_selection",
            "At least one normalized table must be selected for schema analysis.",
        )
    if len(tables) > MAX_TABLES:
        raise SchemaAnalysisError(
            "resource_limit",
            f"Schema analysis is limited to {MAX_TABLES} selected tables.",
        )

    retained_value_budget = [0, 0]
    row_budget = [0]
    collected = [
        _collect_table(table, retained_value_budget, row_budget)
        for table in tables
    ]
    candidate_pairs, candidates_complete = _candidate_pairs(collected)
    relationships = [
        _relationship(collected, candidate) for candidate in candidate_pairs
    ]
    related_columns = {
        (table_index, column_index)
        for relationship in relationships
        if relationship["status"] == "inferred"
        for table_index, column_index in (
            (
                relationship["_source_table_index"],
                relationship["source"]["position"],
            ),
            (
                relationship["_target_table_index"],
                relationship["target"]["position"],
            ),
        )
    }
    for table_index, table in enumerate(collected):
        for column in table["_columns"]:
            if (
                column["identifier_name_candidate"]
                and (table_index, column["position"]) not in related_columns
            ):
                column["role_candidates"].append("degenerate_dimension")
                column["semantic_role_status"] = "heuristic_candidates"
    public_relationships = [
        {
            key: value
            for key, value in relation.items()
            if not key.startswith("_")
        }
        for relation in relationships
    ]
    public_tables = [
        {
            key: value
            for key, value in table.items()
            if key not in {"_columns", "_table"}
        }
        for table in collected
    ]
    recommendations = _model_recommendations(collected, relationships)
    for recommendation in recommendations:
        for path in recommendation.get("recommended_join_paths", []):
            path.pop("relationship_status", None)

    limitations = [
        f"Exact distinct tracking is bounded to {MAX_RETAINED_VALUES} retained values and {MAX_RETAINED_VALUES_PER_COLUMN} values per column.",
        f"Analysis scans at most {MAX_TOTAL_ROWS} rows across selected tables.",
        "Relationships are hypotheses from observed values and do not establish business meaning or database integrity.",
        "Model recommendations are conditional; table grain and measure semantics require business confirmation.",
    ]
    warnings = []
    if not candidates_complete:
        warnings.append("relationship_search_limit")
    if any(
        not column["distinct_count_exact"]
        for table in collected
        for column in table["_columns"]
    ):
        warnings.append("distinct_tracking_limit")
    result = {
        "engine_version": SCHEMA_ARCHITECT_VERSION,
        "analysis_method": {
            "scan": "Exact normalized Parquet row scan, bounded by total row and retained-value limits.",
            "distinct_counts": "Exact typed scalar set counts when distinct_count_exact is true; otherwise unavailable.",
            "relationship_overlap": "Exact typed scalar set intersection only when both columns retain exact distinct values.",
            "numeric_comparison": "Numeric scalar values are canonicalized through Decimal for equality comparison only; normalized Parquet values are never changed.",
            "column_roles": "Role candidates use only column names, normalized types, uniqueness, and observed relationship participation; they do not establish business semantics. Event grain remains unconfirmed.",
            "candidate_generation": "Normalized exact names and identifier suffix aliases; fuzzy semantic matching is not attempted.",
            "confidence_score_formula": "0.30 name similarity + 0.20 type compatibility + 0.20 target candidate key + 0.15 source coverage + 0.05 observed overlap + 0.10 source non-null rate.",
            "confidence_semantics": {
                "high": "Deterministic evidence score >= 0.85; still an inference, not a verified constraint.",
                "medium": "Deterministic evidence score >= 0.65 and < 0.85.",
                "low": "Deterministic evidence score < 0.65.",
            },
        },
        "schema": {
            "tables": public_tables,
            "table_count": len(public_tables),
            "rows_scanned": row_budget[0],
            "retained_distinct_values": retained_value_budget[0],
            "retained_value_bytes": retained_value_budget[1],
        },
        "relationships": public_relationships,
        "model_recommendations": recommendations,
        "clarification_questions": sorted(
            {
                question
                for recommendation in recommendations
                for question in recommendation["required_clarifications"]
            }
        ),
        "limitations": limitations,
        "warnings": warnings,
    }
    try:
        result_size = len(
            json.dumps(
                result,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        raise SchemaAnalysisError(
            "invalid_result",
            "Schema analysis produced non-JSON result data.",
        ) from None
    if result_size > MAX_RESULT_BYTES:
        raise SchemaAnalysisError(
            "resource_limit",
            "Schema-analysis results exceed the 10 MiB response-size limit.",
        )
    return result
