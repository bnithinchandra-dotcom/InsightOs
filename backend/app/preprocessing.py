import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from itertools import islice
from typing import Any, Iterable, Literal, Protocol

import pyarrow as pa
import pyarrow.compute as pc

from app.execution import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
    estimate_decoded_python_string_bytes,
)
from app.provenance import arrow_table_fingerprint, build_provenance, canonical_sha256

from app.quality_catalog import (
    QUALITY_RULE_STATUSES,
    get_quality_rule_catalog_entry,
    matches_configuration_schema,
)


TRANSFORMATION_CATEGORIES = frozenset({"BA", "DA", "structural"})
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_VERSION = re.compile(r"^\d+(?:\.\d+){0,2}$")
_MAX_TEXT_LENGTH = 512
_MAX_RESOURCE_LIMITS = 32


class TransformationRegistryError(ValueError):
    """Raised when transformation metadata or dispatch requirements are invalid."""


class TransformationExecutor(Protocol):
    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> dict | None: ...

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class TransformationOperation:
    operation_id: str
    version: str
    category: str
    status: str
    input_requirements: tuple[str, ...]
    output_requirements: tuple[str, ...]
    prerequisites: tuple[str, ...]
    configuration_schema: dict | None
    configuration_defaults: dict | None
    requires_fit: bool
    changes_rows: bool
    changes_columns: bool
    changes_values: bool
    required_approvals: tuple[str, ...]
    resource_limits: dict[str, int]
    catalog_reference: str | None = None
    changes_metadata: bool = False


@dataclass(frozen=True)
class _Registration:
    operation: TransformationOperation
    executor: TransformationExecutor | None


def _validate_text_items(items: tuple[str, ...], field_name: str) -> None:
    if not isinstance(items, tuple) or any(
        not isinstance(item, str)
        or not item.strip()
        or len(item) > _MAX_TEXT_LENGTH
        for item in items
    ):
        raise TransformationRegistryError(
            f"Transformation {field_name} metadata is invalid."
        )
    if field_name in {"input", "output"} and not items:
        raise TransformationRegistryError(
            f"Transformation {field_name} requirements cannot be empty."
        )


def _validate_operation(operation: TransformationOperation) -> None:
    if (
        not isinstance(operation, TransformationOperation)
        or not isinstance(operation.operation_id, str)
        or len(operation.operation_id) > 480
        or _OPERATION_ID.fullmatch(operation.operation_id) is None
        or not isinstance(operation.version, str)
        or len(operation.version) > 32
        or _VERSION.fullmatch(operation.version) is None
        or not isinstance(operation.category, str)
        or operation.category not in TRANSFORMATION_CATEGORIES
        or not isinstance(operation.status, str)
        or operation.status not in QUALITY_RULE_STATUSES
        or not isinstance(operation.requires_fit, bool)
        or not isinstance(operation.changes_rows, bool)
        or not isinstance(operation.changes_columns, bool)
        or not isinstance(operation.changes_values, bool)
        or not isinstance(operation.changes_metadata, bool)
        or not any(
            (
                operation.changes_rows,
                operation.changes_columns,
                operation.changes_values,
                operation.changes_metadata,
            )
        )
    ):
        raise TransformationRegistryError(
            "Transformation operation metadata is invalid."
        )

    _validate_text_items(operation.input_requirements, "input")
    _validate_text_items(operation.output_requirements, "output")
    _validate_text_items(operation.prerequisites, "prerequisite")
    _validate_text_items(operation.required_approvals, "approval")
    if (
        not isinstance(operation.resource_limits, dict)
        or not 1 <= len(operation.resource_limits) <= _MAX_RESOURCE_LIMITS
        or any(
            not isinstance(name, str)
            or not name.strip()
            or len(name) > _MAX_TEXT_LENGTH
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            for name, limit in operation.resource_limits.items()
        )
    ):
        raise TransformationRegistryError(
            "Transformation resource limits must be named positive integers."
        )

    if operation.catalog_reference is not None:
        if not isinstance(operation.catalog_reference, str):
            raise TransformationRegistryError(
                "Transformation catalog reference is invalid."
            )
        catalog_entry = get_quality_rule_catalog_entry(
            operation.catalog_reference
        )
        if (
            catalog_entry is None
            or "transformation"
            not in (
                catalog_entry["supported_operations"]
                + catalog_entry["planned_operations"]
            )
            or catalog_entry["status"] != operation.status
        ):
            raise TransformationRegistryError(
                "Transformation catalog reference is missing or inconsistent."
            )

    if operation.status == "implemented":
        schema = operation.configuration_schema
        defaults = operation.configuration_defaults
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "object"
            or not isinstance(defaults, dict)
            or not matches_configuration_schema(defaults, schema)
        ):
            raise TransformationRegistryError(
                "Implemented transformations need a valid object configuration schema and defaults."
            )
    elif (
        operation.configuration_schema is not None
        or operation.configuration_defaults is not None
    ):
        raise TransformationRegistryError(
            "Non-implemented transformations cannot declare executable configuration."
        )


def _validate_executor(
    operation: TransformationOperation,
    executor: TransformationExecutor | None,
) -> None:
    if operation.status == "implemented":
        if executor is None or not callable(getattr(executor, "fit", None)) or not callable(
            getattr(executor, "transform", None)
        ):
            raise TransformationRegistryError(
                "Implemented transformations need a fit/transform executor."
            )
    elif executor is not None:
        raise TransformationRegistryError(
            "Partial, planned, and unsupported transformations cannot have an executor."
        )


_MAX_TRANSFORMATION_INPUT_BYTES = 64 * 1024 * 1024
_MAX_TRANSFORMATION_ROWS = 1_000_000
_MAX_TRANSFORMATION_COLUMNS = 256
_MAX_TRANSFORMATION_SELECTED_COLUMNS = 64


def _columns_schema():
    return {
        "type": "array",
        "minItems": 0,
        "maxItems": _MAX_TRANSFORMATION_SELECTED_COLUMNS,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1},
    }


def _object_schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STRING_NORMALIZATION_SCHEMA = _object_schema(
    {
        "columns": _columns_schema(),
        "trim": {"type": "boolean"},
        "case": {
            "type": "string",
            "enum": ["none", "lower", "upper"],
        },
        "empty_strings_as_missing": {"type": "boolean"},
    }
)
_MISSING_VALUE_SCHEMA = _object_schema(
    {
        "columns": _columns_schema(),
        "strategy": {
            "type": "string",
            "enum": [
                "leave",
                "drop_rows",
                "fill_constant",
                "mean",
                "median",
                "most_frequent",
            ],
        },
        "empty_strings_as_missing": {"type": "boolean"},
        "constant_string": {"type": "string"},
        "constant_number": {"type": "number"},
        "constant_boolean": {"type": "boolean"},
    }
)
_NUMERIC_OUTLIER_SCHEMA = _object_schema(
    {
        "columns": _columns_schema(),
        "strategy": {
            "type": "string",
            "enum": ["leave", "clip_bounds", "iqr_clip"],
        },
        "lower_bound": {"type": "number"},
        "upper_bound": {"type": "number"},
        "iqr_multiplier": {"type": "number", "minimum": 0, "maximum": 100},
    }
)
_NUMERIC_SCALING_SCHEMA = _object_schema(
    {
        "columns": _columns_schema(),
        "strategy": {"type": "string", "enum": ["standard", "min_max"]},
        "feature_min": {"type": "number"},
        "feature_max": {"type": "number"},
    }
)
_PREDICATE_SCHEMA = _object_schema(
    {
        "column": {"type": "string", "minLength": 1},
        "operator": {
            "type": "string",
            "enum": [
                "eq",
                "ne",
                "in",
                "not_in",
                "gt",
                "gte",
                "lt",
                "lte",
                "is_null",
                "is_not_null",
            ],
        },
        "value_string": {"type": "string"},
        "value_number": {"type": "number"},
        "value_boolean": {"type": "boolean"},
        "values_string": {
            "type": "array",
            "maxItems": 256,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "values_number": {
            "type": "array",
            "maxItems": 256,
            "uniqueItems": True,
            "items": {"type": "number"},
        },
        "values_boolean": {
            "type": "array",
            "maxItems": 2,
            "uniqueItems": True,
            "items": {"type": "boolean"},
        },
    },
    ("column", "operator"),
)
_BUSINESS_FILTER_SCHEMA = _object_schema(
    {
        "predicates": {
            "type": "array",
            "minItems": 0,
            "maxItems": 64,
            "items": _PREDICATE_SCHEMA,
        },
        "composition": {"type": "string", "enum": ["all", "any"]},
        "null_policy": {"type": "string", "enum": ["exclude", "include"]},
        "reason": {"type": "string", "minLength": 1},
    },
)
_CATEGORY_MAPPING_SCHEMA = _object_schema(
    {
        "column": {"type": "string", "minLength": 1},
        "mappings": {
            "type": "array",
            "minItems": 0,
            "maxItems": 256,
            "items": _object_schema(
                {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
                ("from", "to"),
            ),
        },
        "unmapped_policy": {
            "type": "string",
            "enum": ["preserve", "constant", "error"],
        },
        "unknown_value": {"type": "string"},
        "null_policy": {"type": "string", "enum": ["preserve", "constant", "error"]},
        "null_value": {"type": "string"},
    },
)
_DERIVED_METRIC_SCHEMA = _object_schema(
    {
        "left_column": {"type": "string", "minLength": 1},
        "operator": {
            "type": "string",
            "enum": ["add", "subtract", "multiply", "divide"],
        },
        "right_column": {"type": "string", "minLength": 1},
        "output_column": {"type": "string", "minLength": 1},
        "divide_by_zero": {"type": "string", "enum": ["error", "null"]},
    },
)
_VALIDATION_RULE_SCHEMA = _object_schema(
    {
        "column": {"type": "string", "minLength": 1},
        "check": {
            "type": "string",
            "enum": [
                "required",
                "allowed_values",
                "numeric_range",
                "less_than",
                "less_or_equal",
                "greater_than",
                "greater_or_equal",
                "equals_column",
            ],
        },
        "values": {
            "type": "array",
            "minItems": 1,
            "maxItems": 256,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "other_column": {"type": "string", "minLength": 1},
    },
    ("column", "check"),
)
_BUSINESS_VALIDATION_SCHEMA = _object_schema(
    {
        "rules": {
            "type": "array",
            "minItems": 0,
            "maxItems": 64,
            "items": _VALIDATION_RULE_SCHEMA,
        },
        "empty_strings_are_missing": {"type": "boolean"},
    },
)


def _operation_configuration(
    operation: TransformationOperation,
    configuration: dict | None,
) -> dict:
    if configuration is None:
        supplied = {}
    elif isinstance(configuration, dict):
        supplied = configuration
    else:
        raise TransformationRegistryError(
            "Transformation configuration must be an object."
        )
    if not matches_configuration_schema(
        supplied,
        operation.configuration_schema,
    ):
        raise TransformationRegistryError(
            "Transformation configuration does not match its schema."
        )
    resolved = copy.deepcopy(operation.configuration_defaults)
    resolved.update(copy.deepcopy(supplied))
    try:
        encoded = json.dumps(
            resolved,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise TransformationRegistryError(
            "Transformation configuration cannot be represented safely."
        ) from error
    if len(encoded) > 64 * 1024:
        raise TransformationRegistryError(
            "Transformation configuration exceeds its 64 KiB limit."
        )
    if any(
        not isinstance(name, str)
        or not name.strip()
        or len(name) > _MAX_TEXT_LENGTH
        for name in resolved.get("columns", [])
    ):
        raise TransformationRegistryError(
            "Transformation column names must be no longer than 512 characters."
        )
    if not _configuration_strings_are_bounded(resolved):
        raise TransformationRegistryError(
            "Transformation text fields must not exceed 512 characters."
        )
    return resolved


def _configuration_strings_are_bounded(value, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if isinstance(value, str):
        return len(value) <= _MAX_TEXT_LENGTH
    if isinstance(value, list):
        return all(
            _configuration_strings_are_bounded(item, depth + 1)
            for item in value
        )
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and len(key) <= _MAX_TEXT_LENGTH
            and _configuration_strings_are_bounded(item, depth + 1)
            for key, item in value.items()
        )
    return True


def _require_configuration_fields(
    configuration: dict,
    required: tuple[str, ...],
    operation: str,
) -> None:
    missing = [key for key in required if key not in configuration]
    if missing:
        raise TransformationRegistryError(
            f"{operation} configuration is missing required fields: "
            + ", ".join(missing)
            + "."
        )


def _validate_table(
    data: Any,
    operation: TransformationOperation,
) -> pa.Table:
    if not isinstance(data, pa.Table):
        raise TransformationRegistryError(
            "Transformations require a PyArrow Table."
        )
    limits = operation.resource_limits
    if (
        data.num_rows > limits["max_rows"]
        or data.num_columns > limits["max_columns"]
        or data.nbytes > limits["max_input_bytes"]
    ):
        raise TransformationRegistryError(
            "Transformation input exceeds its configured row, column, or byte limit."
        )
    names = data.schema.names
    if any(not name.strip() for name in names) or len(names) != len(set(names)):
        raise TransformationRegistryError(
            "Transformation input must have unique, non-empty column names."
        )
    return data


def _selected_indices(
    data: pa.Table,
    configuration: dict,
) -> list[int]:
    indices = []
    for name in configuration["columns"]:
        index = data.schema.get_field_index(name)
        if index < 0:
            raise TransformationRegistryError(
                f"Selected transformation column {name!r} was not found."
            )
        indices.append(index)
    return indices


def _is_string_type(value_type: pa.DataType) -> bool:
    return pa.types.is_string(value_type) or pa.types.is_large_string(value_type)


def _consume_decoded_string_budget(
    values: pa.Array | pa.ChunkedArray,
    budget: ExecutionBudget,
) -> None:
    try:
        budget.consume(
            "decoded_string_bytes",
            estimate_decoded_python_string_bytes(values),
        )
    except ExecutionBudgetExceeded as error:
        raise TransformationRegistryError(
            "Transformation decoded string values exceed the configured byte limit."
        ) from error


def _arrow_to_pylist(values: pa.ChunkedArray) -> list:
    return values.to_pylist()


def _is_numeric_type(value_type: pa.DataType) -> bool:
    return pa.types.is_integer(value_type) or pa.types.is_floating(value_type)


def _require_numeric_column(field: pa.Field, values: pa.ChunkedArray) -> None:
    if not _is_numeric_type(field.type):
        raise TransformationRegistryError(
            f"Column {field.name!r} must have an integer or floating-point type."
        )
    if pa.types.is_floating(field.type):
        finite = pc.fill_null(pc.is_finite(values), True)
        invalid = pc.invert(finite)
        if pc.any(invalid).as_py():
            raise TransformationRegistryError(
                f"Column {field.name!r} contains a non-finite numeric value."
            )


def _schema_fingerprint(data: pa.Table) -> list[list[str]]:
    return [[field.name, str(field.type)] for field in data.schema]


def _configuration_fingerprint(configuration: dict) -> str:
    try:
        encoded = json.dumps(
            configuration,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise TransformationRegistryError(
            "Transformation configuration cannot be represented safely."
        ) from error
    return hashlib.sha256(encoded).hexdigest()


def _attach_transformation_metadata(
    data: pa.Table,
    operation: TransformationOperation,
    configuration: dict,
    summary: dict,
) -> pa.Table:
    _validate_table(data, operation)
    metadata = dict(data.schema.metadata or {})
    lineage_key = b"insightos.transformation_lineage"
    previous = metadata.get(lineage_key)
    if previous is None:
        lineage = []
    else:
        try:
            lineage = json.loads(previous.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransformationRegistryError(
                "Existing transformation lineage metadata is invalid."
            ) from error
        if not isinstance(lineage, list) or len(lineage) >= 32:
            raise TransformationRegistryError(
                "Transformation lineage is invalid or exceeds 32 operations."
            )
    lineage.append(
        {
            "operation_id": operation.operation_id,
            "version": operation.version,
            "configuration_sha256": _configuration_fingerprint(configuration),
            "summary": summary,
        }
    )
    try:
        encoded = json.dumps(
            lineage,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise TransformationRegistryError(
            "Transformation summary cannot be represented safely."
        ) from error
    if len(encoded) > 16 * 1024:
        raise TransformationRegistryError(
            "Transformation lineage metadata exceeds its 16 KiB limit."
        )
    metadata[lineage_key] = encoded
    return data.replace_schema_metadata(metadata)


def _fitted_context(
    operation: TransformationOperation,
    configuration: dict,
    data: pa.Table,
    parameters: dict,
) -> dict:
    parameters = copy.deepcopy(parameters)
    return {
        "operation_id": operation.operation_id,
        "version": operation.version,
        "configuration_sha256": _configuration_fingerprint(configuration),
        "parameters_sha256": _configuration_fingerprint(parameters),
        "schema": _schema_fingerprint(data),
        "parameters": parameters,
    }


def _validate_fitted_context(
    context: dict | None,
    operation: TransformationOperation,
    configuration: dict,
    data: pa.Table,
) -> dict:
    parameters = _validate_fitted_context_binding(
        context,
        operation,
        configuration,
    )
    if context["schema"] != _schema_fingerprint(data):
        raise TransformationRegistryError(
            "Fitted transformation context does not match this operation, configuration, or input schema."
        )
    return parameters


def _validate_fitted_context_binding(
    context: dict | None,
    operation: TransformationOperation,
    configuration: dict,
) -> dict:
    if not isinstance(context, dict):
        raise TransformationRegistryError(
            "Transformation must be fitted before transform is called."
        )
    if (
        context.get("operation_id") != operation.operation_id
        or context.get("version") != operation.version
        or context.get("configuration_sha256")
        != _configuration_fingerprint(configuration)
        or not isinstance(context.get("schema"), list)
        or any(
            not isinstance(field, list)
            or len(field) != 2
            or any(not isinstance(value, str) for value in field)
            for field in context["schema"]
        )
        or not isinstance(context.get("parameters"), dict)
        or context.get("parameters_sha256")
        != _configuration_fingerprint(context["parameters"])
    ):
        raise TransformationRegistryError(
            "Fitted transformation context does not match this operation, configuration, or input schema."
        )
    return context["parameters"]


def _replace_column(
    data: pa.Table,
    index: int,
    values: pa.Array | pa.ChunkedArray,
    *,
    value_type: pa.DataType | None = None,
) -> pa.Table:
    field = data.schema.field(index)
    if value_type is not None:
        field = pa.field(
            field.name,
            value_type,
            nullable=field.nullable,
            metadata=field.metadata,
        )
    return data.set_column(index, field, values)


def _numeric_value_count(values: pa.ChunkedArray) -> int:
    count = pc.sum(pc.cast(pc.is_valid(values), pa.int64())).as_py()
    return count or 0


def _quantiles(
    values: pa.ChunkedArray,
    quantiles: list[float],
) -> list[float]:
    result = pc.quantile(
        values,
        q=quantiles,
        interpolation="linear",
    )
    return [item.as_py() for item in result]


class _StringNormalization:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> None:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        for index in _selected_indices(table, resolved):
            if not _is_string_type(table.schema.field(index).type):
                raise TransformationRegistryError(
                    f"Column {table.schema.field(index).name!r} must have a string type."
                )
        return None

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        output = table
        for index in _selected_indices(table, resolved):
            field = table.schema.field(index)
            if not _is_string_type(field.type):
                raise TransformationRegistryError(
                    f"Column {field.name!r} must have a string type."
                )
            values = table.column(index)
            if resolved["trim"]:
                values = pc.utf8_trim_whitespace(values)
            if resolved["case"] == "lower":
                values = pc.utf8_lower(values)
            elif resolved["case"] == "upper":
                values = pc.utf8_upper(values)
            if resolved["empty_strings_as_missing"]:
                values = pc.if_else(
                    pc.equal(values, ""),
                    pa.scalar(None, type=field.type),
                    values,
                )
            output = _replace_column(output, index, values)
        return output


class _MissingValueHandling:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> dict:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        indices = _selected_indices(table, resolved)
        strategy = resolved["strategy"]
        constant_keys = {
            key
            for key in (
                "constant_string",
                "constant_number",
                "constant_boolean",
            )
            if key in resolved
        }
        if strategy != "fill_constant" and constant_keys:
            raise TransformationRegistryError(
                "Typed constant values are valid only for fill_constant imputation."
            )
        if strategy == "leave" and resolved["empty_strings_as_missing"]:
            raise TransformationRegistryError(
                "empty_strings_as_missing is not applicable to the leave strategy."
            )
        constant = (
            self._constant_value(resolved)
            if strategy == "fill_constant" and indices
            else None
        )
        parameters = {}
        decoded_values_budget = ExecutionBudget(
            {"decoded_string_bytes": _MAX_TRANSFORMATION_INPUT_BYTES}
        )

        for index in indices:
            field = table.schema.field(index)
            values = table.column(index)
            if strategy in {"mean", "median"}:
                _require_numeric_column(field, values)
                count = _numeric_value_count(values)
                if count == 0:
                    raise TransformationRegistryError(
                        f"Column {field.name!r} has no observed values for {strategy} imputation."
                    )
                if strategy == "mean":
                    learned = pc.mean(values).as_py()
                else:
                    learned = _quantiles(values, [0.5])[0]
                if not math.isfinite(learned):
                    raise TransformationRegistryError(
                        f"Column {field.name!r} produced a non-finite imputation statistic."
                    )
                parameters[field.name] = learned
            elif strategy == "most_frequent":
                if not (_is_string_type(field.type) or pa.types.is_boolean(field.type)):
                    raise TransformationRegistryError(
                        f"Column {field.name!r} must be string or boolean for most-frequent imputation."
                    )
                observed = self._observed_values(
                    values,
                    field,
                    resolved["empty_strings_as_missing"],
                    decoded_values_budget,
                )
                if not observed:
                    raise TransformationRegistryError(
                        f"Column {field.name!r} has no observed values for most-frequent imputation."
                    )
                counts = {}
                for value in observed:
                    counts[value] = counts.get(value, 0) + 1
                highest_count = max(counts.values())
                parameters[field.name] = min(
                    value for value, count in counts.items() if count == highest_count
                )
            elif strategy == "fill_constant":
                self._validate_constant(field, constant)

        return _fitted_context(self.operation, resolved, table, parameters)

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        parameters = _validate_fitted_context(
            context,
            self.operation,
            resolved,
            table,
        )
        indices = _selected_indices(table, resolved)
        strategy = resolved["strategy"]
        if strategy == "leave" or not indices:
            return table
        if strategy == "fill_constant":
            constant = self._constant_value(resolved)
        else:
            constant = None

        if strategy == "drop_rows":
            keep = pa.array([True] * table.num_rows, type=pa.bool_())
            for index in indices:
                field = table.schema.field(index)
                values = table.column(index)
                missing = self._missing_mask(
                    values,
                    field,
                    resolved["empty_strings_as_missing"],
                )
                keep = pc.and_kleene(keep, pc.invert(missing))
            return table.filter(pc.fill_null(keep, False))

        output = table
        for index in indices:
            field = table.schema.field(index)
            values = table.column(index)
            if strategy in {"mean", "median"}:
                learned = parameters.get(field.name)
                if (
                    isinstance(learned, bool)
                    or not isinstance(learned, (int, float))
                    or not math.isfinite(learned)
                ):
                    raise TransformationRegistryError(
                        f"Fitted imputation statistic for {field.name!r} is invalid."
                    )
                scalar = pa.scalar(learned, type=pa.float64())
                converted = pc.cast(values, pa.float64())
                filled = pc.if_else(
                    self._missing_mask(
                        values,
                        field,
                        resolved["empty_strings_as_missing"],
                    ),
                    scalar,
                    converted,
                )
                output = _replace_column(
                    output,
                    index,
                    filled,
                    value_type=pa.float64(),
                )
            elif strategy == "most_frequent":
                learned = parameters.get(field.name)
                if learned is None:
                    raise TransformationRegistryError(
                        f"Fitted most-frequent value for {field.name!r} is invalid."
                    )
                scalar = pa.scalar(learned, type=field.type)
                filled = pc.if_else(
                    self._missing_mask(
                        values,
                        field,
                        resolved["empty_strings_as_missing"],
                    ),
                    scalar,
                    values,
                )
                output = _replace_column(output, index, filled)
            elif strategy == "fill_constant":
                scalar = self._constant_scalar(field, constant)
                filled = pc.if_else(
                    self._missing_mask(
                        values,
                        field,
                        resolved["empty_strings_as_missing"],
                    ),
                    scalar,
                    values,
                )
                output = _replace_column(output, index, filled)
        return output

    @staticmethod
    def _missing_mask(
        values: pa.ChunkedArray,
        field: pa.Field,
        empty_strings_as_missing: bool,
    ) -> pa.ChunkedArray | pa.Array:
        missing = pc.is_null(values)
        if empty_strings_as_missing and _is_string_type(field.type):
            missing = pc.or_kleene(missing, pc.equal(values, ""))
        return pc.fill_null(missing, False)

    @staticmethod
    def _observed_values(
        values: pa.ChunkedArray,
        field: pa.Field,
        empty_strings_as_missing: bool,
        decoded_values_budget: ExecutionBudget,
    ) -> list[str | bool]:
        if _is_string_type(field.type):
            _consume_decoded_string_budget(values, decoded_values_budget)
        observed = []
        for value in _arrow_to_pylist(values):
            if value is None:
                continue
            if empty_strings_as_missing and _is_string_type(field.type) and value == "":
                continue
            observed.append(value)
        return observed

    @staticmethod
    def _constant_value(configuration: dict):
        keys = {
            key
            for key in (
                "constant_string",
                "constant_number",
                "constant_boolean",
            )
            if key in configuration
        }
        if len(keys) != 1:
            raise TransformationRegistryError(
                "Constant imputation requires exactly one typed constant value."
            )
        return configuration[next(iter(keys))]

    @staticmethod
    def _validate_constant(field: pa.Field, value) -> None:
        compatible = False
        if _is_string_type(field.type):
            compatible = isinstance(value, str)
        elif pa.types.is_boolean(field.type):
            compatible = isinstance(value, bool)
        elif pa.types.is_integer(field.type):
            compatible = isinstance(value, int) and not isinstance(value, bool)
        elif pa.types.is_floating(field.type):
            compatible = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        if not compatible:
            raise TransformationRegistryError(
                f"The configured constant is incompatible with column {field.name!r}."
            )
        try:
            scalar = pa.scalar(value, type=field.type)
        except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
            raise TransformationRegistryError(
                f"The configured constant cannot be represented by column {field.name!r}."
            ) from error
        if _is_numeric_type(field.type) and isinstance(scalar.as_py(), float):
            if not math.isfinite(scalar.as_py()):
                raise TransformationRegistryError(
                    f"The configured constant for {field.name!r} must be finite."
                )

    @classmethod
    def _constant_scalar(cls, field: pa.Field, value) -> pa.Scalar:
        cls._validate_constant(field, value)
        try:
            return pa.scalar(value, type=field.type)
        except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
            raise TransformationRegistryError(
                f"The configured constant cannot be represented by column {field.name!r}."
            ) from error


class _NumericOutlierHandling:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> dict:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        indices = _selected_indices(table, resolved)
        strategy = resolved["strategy"]

        if strategy == "clip_bounds":
            if "iqr_multiplier" in resolved:
                raise TransformationRegistryError(
                    "iqr_multiplier is valid only for iqr_clip."
                )
            lower, upper = self._explicit_bounds(resolved)
        elif strategy == "iqr_clip":
            if "lower_bound" in resolved or "upper_bound" in resolved:
                raise TransformationRegistryError(
                    "Explicit bounds are valid only for clip_bounds."
                )
            multiplier = resolved.get("iqr_multiplier")
            if (
                isinstance(multiplier, bool)
                or not isinstance(multiplier, (int, float))
                or not math.isfinite(multiplier)
                or multiplier < 0
                or multiplier > 100
            ):
                raise TransformationRegistryError(
                    "IQR clipping requires a finite multiplier between 0 and 100."
                )
        else:
            if any(
                key in resolved
                for key in ("lower_bound", "upper_bound", "iqr_multiplier")
            ):
                raise TransformationRegistryError(
                    "Clipping bounds and IQR multipliers require a clipping strategy."
                )
            lower = upper = None

        parameters = {}
        for index in indices:
            field = table.schema.field(index)
            values = table.column(index)
            _require_numeric_column(field, values)
            if strategy == "clip_bounds":
                parameters[field.name] = {"lower": lower, "upper": upper}
            elif strategy == "iqr_clip":
                if _numeric_value_count(values) < 4:
                    raise TransformationRegistryError(
                        f"Column {field.name!r} needs at least four observed values for IQR clipping."
                    )
                q1, q3 = _quantiles(values, [0.25, 0.75])
                spread = q3 - q1
                lower_bound = q1 - multiplier * spread
                upper_bound = q3 + multiplier * spread
                if not math.isfinite(lower_bound) or not math.isfinite(upper_bound):
                    raise TransformationRegistryError(
                        f"Column {field.name!r} produced non-finite IQR bounds."
                    )
                parameters[field.name] = {
                    "lower": lower_bound,
                    "upper": upper_bound,
                }

        return _fitted_context(self.operation, resolved, table, parameters)

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        parameters = _validate_fitted_context(
            context,
            self.operation,
            resolved,
            table,
        )
        strategy = resolved["strategy"]
        if strategy == "leave":
            return table

        output = table
        for index in _selected_indices(table, resolved):
            field = table.schema.field(index)
            values = table.column(index)
            _require_numeric_column(field, values)
            bounds = parameters.get(field.name)
            if (
                not isinstance(bounds, dict)
                or isinstance(bounds.get("lower"), bool)
                or not isinstance(bounds.get("lower"), (int, float))
                or isinstance(bounds.get("upper"), bool)
                or not isinstance(bounds.get("upper"), (int, float))
                or not math.isfinite(bounds["lower"])
                or not math.isfinite(bounds["upper"])
                or bounds["lower"] > bounds["upper"]
            ):
                raise TransformationRegistryError(
                    f"Fitted clipping bounds for {field.name!r} are invalid."
                )
            lower = pa.scalar(bounds["lower"], type=pa.float64())
            upper = pa.scalar(bounds["upper"], type=pa.float64())
            numeric_values = pc.cast(values, pa.float64())
            clipped = pc.if_else(
                pc.less(numeric_values, lower),
                lower,
                pc.if_else(
                    pc.greater(numeric_values, upper),
                    upper,
                    numeric_values,
                ),
            )
            output = _replace_column(
                output,
                index,
                clipped,
                value_type=pa.float64(),
            )
        return output

    @staticmethod
    def _explicit_bounds(configuration: dict) -> tuple[float, float]:
        lower = configuration.get("lower_bound")
        upper = configuration.get("upper_bound")
        if (
            isinstance(lower, bool)
            or not isinstance(lower, (int, float))
            or isinstance(upper, bool)
            or not isinstance(upper, (int, float))
            or not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower >= upper
        ):
            raise TransformationRegistryError(
                "Explicit clipping requires finite lower and upper bounds with lower < upper."
            )
        return float(lower), float(upper)


class _NumericScaling:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> dict:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        indices = _selected_indices(table, resolved)
        strategy = resolved["strategy"]
        parameters = {}

        feature_min = resolved["feature_min"]
        feature_max = resolved["feature_max"]
        if (
            isinstance(feature_min, bool)
            or not isinstance(feature_min, (int, float))
            or isinstance(feature_max, bool)
            or not isinstance(feature_max, (int, float))
            or not math.isfinite(feature_min)
            or not math.isfinite(feature_max)
            or feature_min >= feature_max
        ):
            raise TransformationRegistryError(
                "Min-max scaling requires finite feature bounds with feature_min < feature_max."
            )

        for index in indices:
            field = table.schema.field(index)
            values = table.column(index)
            _require_numeric_column(field, values)
            if _numeric_value_count(values) == 0:
                raise TransformationRegistryError(
                    f"Column {field.name!r} has no observed values for scaling."
                )
            if strategy == "standard":
                mean = pc.mean(values).as_py()
                scale = pc.stddev(values, ddof=0).as_py()
                if not math.isfinite(mean) or not math.isfinite(scale):
                    raise TransformationRegistryError(
                        f"Column {field.name!r} produced non-finite standard-scaling parameters."
                    )
                parameters[field.name] = {"mean": mean, "scale": scale}
            else:
                minimum = float(pc.min(values).as_py())
                maximum = float(pc.max(values).as_py())
                if not math.isfinite(minimum) or not math.isfinite(maximum):
                    raise TransformationRegistryError(
                        f"Column {field.name!r} produced non-finite min-max scaling parameters."
                    )
                parameters[field.name] = {
                    "minimum": minimum,
                    "maximum": maximum,
                    "feature_min": feature_min,
                    "feature_max": feature_max,
                }
        return _fitted_context(self.operation, resolved, table, parameters)

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        parameters = _validate_fitted_context(
            context,
            self.operation,
            resolved,
            table,
        )
        strategy = resolved["strategy"]
        output = table
        for index in _selected_indices(table, resolved):
            field = table.schema.field(index)
            values = table.column(index)
            _require_numeric_column(field, values)
            numeric_values = pc.cast(values, pa.float64())
            fitted = parameters.get(field.name)
            if not isinstance(fitted, dict):
                raise TransformationRegistryError(
                    f"Fitted scaling parameters for {field.name!r} are unavailable."
                )
            if strategy == "standard":
                mean = fitted.get("mean")
                scale = fitted.get("scale")
                if (
                    isinstance(mean, bool)
                    or not isinstance(mean, (int, float))
                    or not math.isfinite(mean)
                    or isinstance(scale, bool)
                    or not isinstance(scale, (int, float))
                    or not math.isfinite(scale)
                    or scale < 0
                ):
                    raise TransformationRegistryError(
                        f"Fitted standard-scaling parameters for {field.name!r} are invalid."
                    )
                if scale == 0:
                    scaled = pc.if_else(
                        pc.is_null(numeric_values),
                        pa.scalar(None, type=pa.float64()),
                        pa.scalar(0.0),
                    )
                else:
                    scaled = pc.divide(
                        pc.subtract(numeric_values, pa.scalar(mean)),
                        pa.scalar(scale),
                    )
            else:
                minimum = fitted.get("minimum")
                maximum = fitted.get("maximum")
                feature_min = fitted.get("feature_min")
                feature_max = fitted.get("feature_max")
                if (
                    any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        for value in (
                            minimum,
                            maximum,
                            feature_min,
                            feature_max,
                        )
                    )
                    or maximum < minimum
                    or feature_max <= feature_min
                ):
                    raise TransformationRegistryError(
                        f"Fitted min-max scaling parameters for {field.name!r} are invalid."
                    )
                if maximum == minimum:
                    scaled = pc.if_else(
                        pc.is_null(numeric_values),
                        pa.scalar(None, type=pa.float64()),
                        pa.scalar(feature_min),
                    )
                else:
                    scaled = pc.add(
                        pc.multiply(
                            pc.divide(
                                pc.subtract(
                                    numeric_values,
                                    pa.scalar(minimum),
                                ),
                                pa.scalar(maximum - minimum),
                            ),
                            pa.scalar(feature_max - feature_min),
                        ),
                        pa.scalar(feature_min),
                    )
            output = _replace_column(
                output,
                index,
                scaled,
                value_type=pa.float64(),
            )
        return output


class _BusinessRuleFilter:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> None:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        self._evaluate(table, resolved, return_masks=False)
        return None

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        keep, review = self._evaluate(table, resolved, return_masks=True)
        if resolved["null_policy"] == "include":
            keep = pc.fill_null(keep, True)
        else:
            keep = pc.fill_null(keep, False)
        output = table.filter(keep)
        retained = output.num_rows
        summary = {
            "rows_before": table.num_rows,
            "rows_retained": retained,
            "rows_filtered": table.num_rows - retained,
            "rows_requiring_review": pc.sum(
                pc.cast(review, pa.int64())
            ).as_py() or 0,
            "reason": resolved["reason"],
            "composition": resolved["composition"],
            "null_policy": resolved["null_policy"],
            "affected_columns": sorted(
                {predicate["column"] for predicate in resolved["predicates"]}
            ),
        }
        return _attach_transformation_metadata(
            output,
            self.operation,
            resolved,
            summary,
        )

    def _evaluate(
        self,
        table: pa.Table,
        configuration: dict,
        *,
        return_masks: bool,
    ):
        _require_configuration_fields(
            configuration,
            ("predicates", "composition", "null_policy", "reason"),
            "Business filter",
        )
        predicates = configuration["predicates"]
        if (
            not predicates
            or configuration["composition"] not in {"all", "any"}
            or configuration["null_policy"] not in {"exclude", "include"}
            or not configuration["reason"].strip()
        ):
            raise TransformationRegistryError(
                "Business filters need a valid composition, null policy, and reason."
            )
        result = (
            pa.array([True] * table.num_rows, type=pa.bool_())
            if configuration["composition"] == "all"
            else pa.array([False] * table.num_rows, type=pa.bool_())
        )
        for predicate in predicates:
            mask = self._predicate_mask(table, predicate)
            result = (
                pc.and_kleene(result, mask)
                if configuration["composition"] == "all"
                else pc.or_kleene(result, mask)
            )
        if return_masks:
            return result, pc.is_null(result)
        return None

    @staticmethod
    def _predicate_value(predicate: dict, field: pa.Field):
        operator = predicate["operator"]
        if operator in {"is_null", "is_not_null"}:
            value_keys = {
                "value_string",
                "value_number",
                "value_boolean",
                "values_string",
                "values_number",
                "values_boolean",
            }
            if value_keys.intersection(predicate):
                raise TransformationRegistryError(
                    f"{operator} does not accept comparison values."
                )
            return None

        if operator in {"in", "not_in"}:
            choices = [
                (key, predicate[key])
                for key in ("values_string", "values_number", "values_boolean")
                if key in predicate
            ]
            if len(choices) != 1 or not choices[0][1]:
                raise TransformationRegistryError(
                    f"{operator} requires exactly one non-empty typed values list."
                )
            key, values = choices[0]
            expected = {
                "values_string": _is_string_type(field.type),
                "values_number": _is_numeric_type(field.type),
                "values_boolean": pa.types.is_boolean(field.type),
            }[key]
            if not expected:
                raise TransformationRegistryError(
                    f"{operator} values are incompatible with column {field.name!r}."
                )
            return [
                _coerce_predicate_scalar(value, field)
                for value in values
            ]

        choices = [
            (key, predicate[key])
            for key in ("value_string", "value_number", "value_boolean")
            if key in predicate
        ]
        if len(choices) != 1:
            raise TransformationRegistryError(
                f"{operator} requires exactly one typed comparison value."
            )
        key, value = choices[0]
        expected = (
            key == "value_string" and _is_string_type(field.type)
            or key == "value_number" and _is_numeric_type(field.type)
            or key == "value_boolean" and pa.types.is_boolean(field.type)
        )
        if not expected:
            raise TransformationRegistryError(
                f"Comparison value is incompatible with column {field.name!r}."
            )
        return _coerce_predicate_scalar(value, field)

    @classmethod
    def _predicate_mask(
        cls,
        table: pa.Table,
        predicate: dict,
    ) -> pa.Array | pa.ChunkedArray:
        if not isinstance(predicate, dict):
            raise TransformationRegistryError(
                "Business filter predicates must be objects."
            )
        operator = predicate.get("operator")
        allowed_fields = {
            "eq": {"column", "operator", "value_string", "value_number", "value_boolean"},
            "ne": {"column", "operator", "value_string", "value_number", "value_boolean"},
            "gt": {"column", "operator", "value_number"},
            "gte": {"column", "operator", "value_number"},
            "lt": {"column", "operator", "value_number"},
            "lte": {"column", "operator", "value_number"},
            "in": {"column", "operator", "values_string", "values_number", "values_boolean"},
            "not_in": {"column", "operator", "values_string", "values_number", "values_boolean"},
            "is_null": {"column", "operator"},
            "is_not_null": {"column", "operator"},
        }.get(operator)
        if allowed_fields is None or set(predicate) - allowed_fields:
            raise TransformationRegistryError(
                f"Business filter predicate {operator!r} has unsupported fields."
            )
        index = table.schema.get_field_index(predicate.get("column"))
        if index < 0:
            raise TransformationRegistryError(
                f"Business filter column {predicate.get('column')!r} was not found."
            )
        field = table.schema.field(index)
        values = table.column(index)
        comparison = cls._predicate_value(predicate, field)
        if operator == "is_null":
            return pc.is_null(values)
        if operator == "is_not_null":
            return pc.is_valid(values)
        if operator in {"gt", "gte", "lt", "lte"} and not _is_numeric_type(
            field.type
        ):
            raise TransformationRegistryError(
                f"{operator} comparisons require a numeric column."
            )
        if operator in {"in", "not_in"}:
            mask = pc.is_in(
                values,
                value_set=pa.array(comparison, type=field.type),
            )
            if operator == "not_in":
                mask = pc.invert(mask)
            return pc.if_else(
                pc.is_null(values),
                pa.scalar(None, type=pa.bool_()),
                mask,
            )
        functions = {
            "eq": pc.equal,
            "ne": pc.not_equal,
            "gt": pc.greater,
            "gte": pc.greater_equal,
            "lt": pc.less,
            "lte": pc.less_equal,
        }
        if operator not in functions:
            raise TransformationRegistryError(
                f"Unsupported business filter operator {operator!r}."
            )
        return functions[operator](values, comparison)


def _coerce_predicate_scalar(value, field: pa.Field) -> pa.Scalar:
    if _is_numeric_type(field.type):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TransformationRegistryError(
                f"Comparison value for {field.name!r} must be numeric."
            )
        if not math.isfinite(value):
            raise TransformationRegistryError(
                f"Comparison value for {field.name!r} must be finite."
            )
        if pa.types.is_integer(field.type):
            if not float(value).is_integer():
                raise TransformationRegistryError(
                    f"Comparison value for integer column {field.name!r} must be integral."
                )
            value = int(value)
    try:
        return pa.scalar(value, type=field.type)
    except (pa.ArrowException, OverflowError, TypeError, ValueError) as error:
        raise TransformationRegistryError(
            f"Comparison value cannot be represented by column {field.name!r}."
        ) from error


class _CategoryMapping:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> None:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        self._mapping(table, resolved)
        return None

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        index, mapping = self._mapping(table, resolved)
        if resolved["unmapped_policy"] not in {"preserve", "constant", "error"}:
            raise TransformationRegistryError("Unknown unmapped category policy.")
        if resolved["null_policy"] not in {"preserve", "constant", "error"}:
            raise TransformationRegistryError("Unknown null category policy.")
        if (
            resolved["unmapped_policy"] == "constant"
            and "unknown_value" not in resolved
        ):
            raise TransformationRegistryError(
                "The constant unmapped policy requires unknown_value."
            )
        if (
            resolved["unmapped_policy"] != "constant"
            and "unknown_value" in resolved
        ):
            raise TransformationRegistryError(
                "unknown_value is valid only with the constant unmapped policy."
            )
        if (
            resolved["null_policy"] == "constant"
            and "null_value" not in resolved
        ):
            raise TransformationRegistryError(
                "The constant null policy requires null_value."
            )
        if (
            resolved["null_policy"] != "constant"
            and "null_value" in resolved
        ):
            raise TransformationRegistryError(
                "null_value is valid only with the constant null policy."
            )
        field = table.schema.field(index)
        source_array = table.column(index)
        decoded_values_budget = ExecutionBudget(
            {"decoded_string_bytes": _MAX_TRANSFORMATION_INPUT_BYTES}
        )
        _consume_decoded_string_budget(source_array, decoded_values_budget)
        source_values = _arrow_to_pylist(source_array)
        null_count = sum(value is None for value in source_values)
        unmapped_count = sum(
            value is not None and value not in mapping
            for value in source_values
        )
        if resolved["unmapped_policy"] == "error" and unmapped_count:
            raise TransformationRegistryError(
                f"Category mapping encountered {unmapped_count} unmapped rows."
            )
        if resolved["null_policy"] == "error" and null_count:
            raise TransformationRegistryError(
                f"Category mapping encountered {null_count} null rows."
            )
        mapped_values = []
        for value in source_values:
            if value is None:
                mapped_values.append(
                    resolved.get("null_value")
                    if resolved["null_policy"] == "constant"
                    else None
                )
            elif value in mapping:
                mapped_values.append(mapping[value])
            elif resolved["unmapped_policy"] == "constant":
                mapped_values.append(resolved["unknown_value"])
            else:
                mapped_values.append(value)
        output = _replace_column(
            table,
            index,
            pa.array(mapped_values, type=pa.string()),
            value_type=pa.string(),
        )
        summary = {
            "affected_columns": [field.name],
            "rows_total": table.num_rows,
            "rows_mapped": table.num_rows - unmapped_count - null_count,
            "rows_unmapped_preserved_or_replaced": unmapped_count,
            "rows_null_preserved_or_replaced": null_count,
            "unmapped_policy": resolved["unmapped_policy"],
            "null_policy": resolved["null_policy"],
        }
        return _attach_transformation_metadata(
            output,
            self.operation,
            resolved,
            summary,
        )

    @staticmethod
    def _mapping(table: pa.Table, configuration: dict) -> tuple[int, dict[str, str]]:
        _require_configuration_fields(
            configuration,
            ("column", "mappings"),
            "Category mapping",
        )
        if not configuration["mappings"]:
            raise TransformationRegistryError(
                "Category mapping needs at least one explicit mapping."
            )
        column = configuration["column"]
        index = table.schema.get_field_index(column)
        if index < 0:
            raise TransformationRegistryError(
                f"Category mapping column {column!r} was not found."
            )
        if not _is_string_type(table.schema.field(index).type):
            raise TransformationRegistryError(
                f"Category mapping column {column!r} must have a string type."
            )
        mapping = {}
        for pair in configuration["mappings"]:
            if set(pair) != {"from", "to"}:
                raise TransformationRegistryError(
                    "Each category mapping must define exactly from and to values."
                )
            source = pair["from"]
            if source in mapping:
                raise TransformationRegistryError(
                    f"Category mapping contains a duplicate or conflicting source value for {column!r}."
                )
            mapping[source] = pair["to"]
        return index, mapping


class _DerivedBusinessMetric:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> None:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        self._columns(table, resolved)
        return None

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        left_index, right_index = self._columns(table, resolved)
        output_name = resolved["output_column"]
        if len(output_name) > _MAX_TEXT_LENGTH or not output_name.strip():
            raise TransformationRegistryError(
                "Derived metric output_column must be non-empty and no longer than 512 characters."
            )
        if table.schema.get_field_index(output_name) >= 0:
            raise TransformationRegistryError(
                f"Derived metric output column {output_name!r} already exists; source columns are never overwritten."
            )
        left_field = table.schema.field(left_index)
        right_field = table.schema.field(right_index)
        _require_numeric_column(left_field, table.column(left_index))
        _require_numeric_column(right_field, table.column(right_index))
        left = pc.cast(table.column(left_index), pa.float64())
        right = pc.cast(table.column(right_index), pa.float64())
        operator = resolved["operator"]
        if operator == "add":
            result = pc.add(left, right)
        elif operator == "subtract":
            result = pc.subtract(left, right)
        elif operator == "multiply":
            result = pc.multiply(left, right)
        elif operator == "divide":
            zero_denominator = pc.equal(right, 0.0)
            zero_count = pc.sum(pc.cast(zero_denominator, pa.int64())).as_py() or 0
            if zero_count and resolved["divide_by_zero"] == "error":
                raise TransformationRegistryError(
                    f"Derived metric division encountered {zero_count} zero denominators."
                )
            result = pc.divide(left, right)
            result = pc.if_else(
                zero_denominator,
                pa.scalar(None, type=pa.float64()),
                result,
            )
        else:
            raise TransformationRegistryError(
                f"Unsupported metric operator {operator!r}."
            )
        finite = pc.fill_null(pc.is_finite(result), True)
        if pc.any(pc.invert(finite)).as_py():
            raise TransformationRegistryError(
                "Derived metric produced a non-finite result."
            )
        output = table.append_column(
            pa.field(output_name, pa.float64(), nullable=True),
            result,
        )
        summary = {
            "affected_columns": [output_name],
            "formula": {
                "left_column": left_field.name,
                "operator": operator,
                "right_column": right_field.name,
                "output_column": output_name,
            },
            "rows_before": table.num_rows,
            "rows_after": output.num_rows,
            "null_result_count": pc.sum(
                pc.cast(pc.is_null(result), pa.int64())
            ).as_py() or 0,
            "divide_by_zero": resolved.get("divide_by_zero"),
        }
        return _attach_transformation_metadata(
            output,
            self.operation,
            resolved,
            summary,
        )

    @staticmethod
    def _columns(table: pa.Table, configuration: dict) -> tuple[int, int]:
        _require_configuration_fields(
            configuration,
            ("left_column", "operator", "right_column", "output_column"),
            "Derived metric",
        )
        indices = []
        for key in ("left_column", "right_column"):
            name = configuration[key]
            index = table.schema.get_field_index(name)
            if index < 0:
                raise TransformationRegistryError(
                    f"Derived metric column {name!r} was not found."
                )
            indices.append(index)
        return indices[0], indices[1]


class _BusinessRuleValidation:
    operation: TransformationOperation

    def fit(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> None:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        self._validate_rules(table, resolved)
        return None

    def transform(
        self,
        data: Any,
        *,
        configuration: dict,
        context: dict | None = None,
    ) -> pa.Table:
        _ = context
        table = _validate_table(data, self.operation)
        resolved = _operation_configuration(self.operation, configuration)
        checked = self._validate_rules(table, resolved)
        findings = []
        for rule, affected, review in checked:
            findings.append(
                {
                    "column": rule["column"],
                    "check": rule["check"],
                    "affected_count": affected,
                    "review_count": review,
                    "denominator_count": table.num_rows,
                }
            )
        summary = {
            "rows_checked": table.num_rows,
            "validation_results": findings,
            "affected_columns": sorted(
                {rule["column"] for rule in resolved["rules"]}
            ),
            "data_modified": False,
        }
        return _attach_transformation_metadata(
            table,
            self.operation,
            resolved,
            summary,
        )

    @staticmethod
    def _validate_rules(table: pa.Table, configuration: dict):
        _require_configuration_fields(
            configuration,
            ("rules",),
            "Business validation",
        )
        if not configuration["rules"]:
            raise TransformationRegistryError(
                "Business validation needs at least one explicit rule."
            )
        results = []
        for rule in configuration["rules"]:
            if not isinstance(rule, dict):
                raise TransformationRegistryError(
                    "Business validation rules must be objects."
                )
            column = rule["column"]
            index = table.schema.get_field_index(column)
            if index < 0:
                raise TransformationRegistryError(
                    f"Business validation column {column!r} was not found."
                )
            field = table.schema.field(index)
            values = table.column(index)
            check = rule["check"]
            allowed_fields = {
                "required": {"column", "check"},
                "allowed_values": {"column", "check", "values"},
                "numeric_range": {"column", "check", "minimum", "maximum"},
                "less_than": {"column", "check", "other_column"},
                "less_or_equal": {"column", "check", "other_column"},
                "greater_than": {"column", "check", "other_column"},
                "greater_or_equal": {"column", "check", "other_column"},
                "equals_column": {"column", "check", "other_column"},
            }.get(check)
            if allowed_fields is None or set(rule) - allowed_fields:
                raise TransformationRegistryError(
                    f"Validation rule {check!r} has unsupported fields."
                )
            required_fields = {
                "required": (),
                "allowed_values": ("values",),
                "numeric_range": (),
                "less_than": ("other_column",),
                "less_or_equal": ("other_column",),
                "greater_than": ("other_column",),
                "greater_or_equal": ("other_column",),
                "equals_column": ("other_column",),
            }[check]
            _require_configuration_fields(
                rule,
                required_fields,
                f"Validation rule {check}",
            )
            missing = pc.is_null(values)
            if (
                configuration["empty_strings_are_missing"]
                and _is_string_type(field.type)
            ):
                missing = pc.or_kleene(missing, pc.equal(values, ""))
            missing = pc.fill_null(missing, False)
            review = 0
            if check == "required":
                affected = pc.sum(pc.cast(missing, pa.int64())).as_py() or 0
            elif check == "allowed_values":
                if not _is_string_type(field.type):
                    raise TransformationRegistryError(
                        f"Allowed-value validation for {column!r} requires a string column."
                    )
                allowed = pa.array(rule["values"], type=field.type)
                valid = pc.is_in(values, value_set=allowed)
                review = pc.sum(pc.cast(missing, pa.int64())).as_py() or 0
                violations = pc.and_kleene(pc.invert(valid), pc.invert(missing))
                affected = pc.sum(
                    pc.cast(pc.fill_null(violations, False), pa.int64())
                ).as_py() or 0
            elif check == "numeric_range":
                if not _is_numeric_type(field.type):
                    raise TransformationRegistryError(
                        f"Numeric-range validation for {column!r} requires a numeric column."
                    )
                lower = rule.get("minimum")
                upper = rule.get("maximum")
                if (
                    lower is None and upper is None
                    or lower is not None
                    and (
                        isinstance(lower, bool)
                        or not isinstance(lower, (int, float))
                        or not math.isfinite(lower)
                    )
                    or upper is not None
                    and (
                        isinstance(upper, bool)
                        or not isinstance(upper, (int, float))
                        or not math.isfinite(upper)
                    )
                    or lower is not None
                    and upper is not None
                    and lower > upper
                ):
                    raise TransformationRegistryError(
                        f"Numeric range for {column!r} needs valid ordered minimum/maximum bounds."
                    )
                violations = pa.array([False] * table.num_rows, type=pa.bool_())
                if lower is not None:
                    violations = pc.or_kleene(
                        violations,
                        pc.less(values, _coerce_predicate_scalar(lower, field)),
                    )
                if upper is not None:
                    violations = pc.or_kleene(
                        violations,
                        pc.greater(values, _coerce_predicate_scalar(upper, field)),
                    )
                review = pc.sum(pc.cast(missing, pa.int64())).as_py() or 0
                violations = pc.and_kleene(violations, pc.invert(missing))
                affected = pc.sum(
                    pc.cast(pc.fill_null(violations, False), pa.int64())
                ).as_py() or 0
            else:
                if "other_column" not in rule:
                    raise TransformationRegistryError(
                        f"Validation rule {check!r} requires other_column."
                    )
                other_name = rule["other_column"]
                other_index = table.schema.get_field_index(other_name)
                if other_index < 0:
                    raise TransformationRegistryError(
                        f"Business validation column {other_name!r} was not found."
                    )
                other_field = table.schema.field(other_index)
                other_values = table.column(other_index)
                if not _is_numeric_type(field.type) or not _is_numeric_type(
                    other_field.type
                ):
                    raise TransformationRegistryError(
                        f"Cross-column comparison {column!r}/{other_name!r} requires numeric columns."
                    )
                _require_numeric_column(field, values)
                _require_numeric_column(other_field, other_values)
                both_missing = pc.or_kleene(missing, pc.is_null(other_values))
                review = pc.sum(
                    pc.cast(pc.fill_null(both_missing, False), pa.int64())
                ).as_py() or 0
                comparison = {
                    "less_than": pc.less,
                    "less_or_equal": pc.less_equal,
                    "greater_than": pc.greater,
                    "greater_or_equal": pc.greater_equal,
                    "equals_column": pc.equal,
                }[check](values, other_values)
                violations = pc.and_kleene(
                    pc.invert(comparison),
                    pc.invert(both_missing),
                )
                affected = pc.sum(
                    pc.cast(pc.fill_null(violations, False), pa.int64())
                ).as_py() or 0
            results.append((rule, affected, review))
        return results


_TRANSFORMATION_RESOURCE_LIMITS = {
    "max_input_bytes": _MAX_TRANSFORMATION_INPUT_BYTES,
    "max_rows": _MAX_TRANSFORMATION_ROWS,
    "max_columns": _MAX_TRANSFORMATION_COLUMNS,
}
_TRANSFORMATION_PREREQUISITES = (
    "pyarrow_table",
    "explicit_column_selection",
)
_BA_PREREQUISITES = ("pyarrow_table", "explicit_business_rule_definition")


class TransformationRegistry:
    def __init__(
        self,
        registrations: Iterable[
            tuple[TransformationOperation, TransformationExecutor | None]
        ] = (),
    ):
        supplied = tuple(registrations)
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], TransformationOperation)
            for item in supplied
        ):
            raise TransformationRegistryError(
                "Transformation registrations must contain operation metadata and an executor."
            )
        for operation, executor in supplied:
            _validate_operation(operation)
            _validate_executor(operation, executor)
        ordered = sorted(supplied, key=lambda item: item[0].operation_id)
        if len({item[0].operation_id for item in ordered}) != len(ordered):
            raise TransformationRegistryError(
                "Transformation operation identifiers must be unique."
            )
        validated = []
        for operation, executor in ordered:
            validated.append(
                _Registration(
                    operation=copy.deepcopy(operation),
                    executor=executor,
                )
            )
        self._registrations = tuple(validated)
        self._by_id = {
            item.operation.operation_id: item for item in self._registrations
        }

    def __iter__(self):
        return iter(
            tuple(copy.deepcopy(item.operation) for item in self._registrations)
        )

    def __len__(self) -> int:
        return len(self._registrations)

    def list_operations(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
    ) -> list[TransformationOperation]:
        if category is not None and category not in TRANSFORMATION_CATEGORIES:
            raise TransformationRegistryError(
                "Unknown transformation category."
            )
        if status is not None and status not in QUALITY_RULE_STATUSES:
            raise TransformationRegistryError(
                "Unknown transformation implementation status."
            )
        return [
            copy.deepcopy(item.operation)
            for item in self._registrations
            if (category is None or item.operation.category == category)
            and (status is None or item.operation.status == status)
        ]

    def resolve_executor(
        self,
        operation_id: str,
        *,
        configuration: dict | None = None,
        available_prerequisites: Iterable[str] = (),
        approved_approvals: Iterable[str] = (),
    ) -> TransformationExecutor:
        registration = self._by_id.get(operation_id)
        if registration is None:
            raise TransformationRegistryError(
                "Unknown transformation operation."
            )
        operation = registration.operation
        if operation.status != "implemented" or registration.executor is None:
            raise TransformationRegistryError(
                "Transformation operation is not implemented and cannot be dispatched."
            )

        supplied_configuration = (
            copy.deepcopy(operation.configuration_defaults)
            if configuration is None
            else configuration
        )
        if not matches_configuration_schema(
            supplied_configuration,
            operation.configuration_schema,
        ):
            raise TransformationRegistryError(
                "Transformation configuration does not match its schema."
            )

        prerequisites = _validated_tokens(
            available_prerequisites,
            "available prerequisites",
        )
        approvals = _validated_tokens(approved_approvals, "approved approvals")
        missing_prerequisites = set(operation.prerequisites) - prerequisites
        if missing_prerequisites:
            raise TransformationRegistryError(
                "Transformation prerequisites are not satisfied."
            )
        missing_approvals = set(operation.required_approvals) - approvals
        if missing_approvals:
            raise TransformationRegistryError(
                "Required transformation approvals are missing."
            )
        return registration.executor


def _validated_tokens(values: Iterable[str], field_name: str) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise TransformationRegistryError(
            f"Transformation {field_name} must be a collection of identifiers."
        )
    try:
        tokens = tuple(values)
    except TypeError as error:
        raise TransformationRegistryError(
            f"Transformation {field_name} must be a collection of identifiers."
        ) from error
    if any(
        not isinstance(token, str)
        or not token.strip()
        or len(token) > _MAX_TEXT_LENGTH
        for token in tokens
    ):
        raise TransformationRegistryError(
            f"Transformation {field_name} contains an invalid identifier."
        )
    return set(tokens)


_STRING_NORMALIZATION = _StringNormalization()
_STRING_NORMALIZATION.operation = TransformationOperation(
    operation_id="da.string_normalization",
    version="1",
    category="DA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit string columns"),
    output_requirements=("PyArrow table with the same rows and schema",),
    prerequisites=_TRANSFORMATION_PREREQUISITES,
    configuration_schema=_STRING_NORMALIZATION_SCHEMA,
    configuration_defaults={
        "columns": [],
        "trim": False,
        "case": "none",
        "empty_strings_as_missing": False,
    },
    requires_fit=False,
    changes_rows=False,
    changes_columns=False,
    changes_values=True,
    required_approvals=("approved_normalization_policy",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
)
_MISSING_VALUE_HANDLING = _MissingValueHandling()
_MISSING_VALUE_HANDLING.operation = TransformationOperation(
    operation_id="da.missing_value_handling",
    version="1",
    category="DA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit columns and strategy"),
    output_requirements=("PyArrow table with configured missing-value handling",),
    prerequisites=_TRANSFORMATION_PREREQUISITES,
    configuration_schema=_MISSING_VALUE_SCHEMA,
    configuration_defaults={
        "columns": [],
        "strategy": "leave",
        "empty_strings_as_missing": False,
    },
    requires_fit=True,
    changes_rows=True,
    changes_columns=True,
    changes_values=True,
    required_approvals=("approved_missing_value_strategy",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
)
_NUMERIC_OUTLIER_HANDLING = _NumericOutlierHandling()
_NUMERIC_OUTLIER_HANDLING.operation = TransformationOperation(
    operation_id="da.numeric_outlier_handling",
    version="1",
    category="DA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit numeric columns and strategy"),
    output_requirements=("PyArrow table with configured numeric values",),
    prerequisites=_TRANSFORMATION_PREREQUISITES,
    configuration_schema=_NUMERIC_OUTLIER_SCHEMA,
    configuration_defaults={"columns": [], "strategy": "leave"},
    requires_fit=True,
    changes_rows=False,
    changes_columns=True,
    changes_values=True,
    required_approvals=("approved_outlier_handling",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
)
_NUMERIC_SCALING = _NumericScaling()
_NUMERIC_SCALING.operation = TransformationOperation(
    operation_id="da.numeric_scaling",
    version="1",
    category="DA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit numeric columns and scaling method"),
    output_requirements=("PyArrow table with selected numeric columns scaled as float64",),
    prerequisites=_TRANSFORMATION_PREREQUISITES,
    configuration_schema=_NUMERIC_SCALING_SCHEMA,
    configuration_defaults={
        "columns": [],
        "strategy": "standard",
        "feature_min": 0.0,
        "feature_max": 1.0,
    },
    requires_fit=True,
    changes_rows=False,
    changes_columns=True,
    changes_values=True,
    required_approvals=("approved_scaling_parameters",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
)

_BUSINESS_RULE_FILTER = _BusinessRuleFilter()
_BUSINESS_RULE_FILTER.operation = TransformationOperation(
    operation_id="ba.business_rule_filter",
    version="1",
    category="BA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit filter predicates"),
    output_requirements=(
        "filtered PyArrow table with row counts and review counts in lineage metadata",
    ),
    prerequisites=_BA_PREREQUISITES,
    configuration_schema=_BUSINESS_FILTER_SCHEMA,
    configuration_defaults={
        "composition": "all",
        "null_policy": "exclude",
    },
    requires_fit=False,
    changes_rows=True,
    changes_columns=False,
    changes_values=False,
    required_approvals=("approved_business_scope_filter",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
    changes_metadata=True,
)
_CATEGORY_MAPPING = _CategoryMapping()
_CATEGORY_MAPPING.operation = TransformationOperation(
    operation_id="ba.category_mapping",
    version="1",
    category="BA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit category mapping table"),
    output_requirements=(
        "PyArrow table with mapped category and mapping-coverage metadata",
    ),
    prerequisites=_BA_PREREQUISITES,
    configuration_schema=_CATEGORY_MAPPING_SCHEMA,
    configuration_defaults={
        "unmapped_policy": "preserve",
        "null_policy": "preserve",
    },
    requires_fit=False,
    changes_rows=False,
    changes_columns=True,
    changes_values=True,
    required_approvals=(
        "approved_category_mapping",
        "approved_category_merging",
    ),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
    changes_metadata=True,
)
_DERIVED_BUSINESS_METRIC = _DerivedBusinessMetric()
_DERIVED_BUSINESS_METRIC.operation = TransformationOperation(
    operation_id="ba.derived_business_metric",
    version="1",
    category="BA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit two-column arithmetic formula"),
    output_requirements=(
        "PyArrow table with a new float64 metric column and formula trace metadata",
    ),
    prerequisites=_BA_PREREQUISITES,
    configuration_schema=_DERIVED_METRIC_SCHEMA,
    configuration_defaults={
        "operator": "add",
        "divide_by_zero": "error",
    },
    requires_fit=False,
    changes_rows=False,
    changes_columns=True,
    changes_values=True,
    required_approvals=("approved_business_metric_definition",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
    changes_metadata=True,
)
_BUSINESS_RULE_VALIDATION = _BusinessRuleValidation()
_BUSINESS_RULE_VALIDATION.operation = TransformationOperation(
    operation_id="ba.business_rule_validation",
    version="1",
    category="BA",
    status="implemented",
    input_requirements=("bounded PyArrow table", "explicit validation rules"),
    output_requirements=(
        "unchanged data table with aggregate validation findings in lineage metadata",
    ),
    prerequisites=_BA_PREREQUISITES,
    configuration_schema=_BUSINESS_VALIDATION_SCHEMA,
    configuration_defaults={"empty_strings_are_missing": False},
    requires_fit=False,
    changes_rows=False,
    changes_columns=False,
    changes_values=False,
    required_approvals=("approved_business_validation_rules",),
    resource_limits=dict(_TRANSFORMATION_RESOURCE_LIMITS),
    changes_metadata=True,
)

DEFAULT_TRANSFORMATION_REGISTRY = TransformationRegistry(
    (
        (_BUSINESS_RULE_FILTER.operation, _BUSINESS_RULE_FILTER),
        (_BUSINESS_RULE_VALIDATION.operation, _BUSINESS_RULE_VALIDATION),
        (_CATEGORY_MAPPING.operation, _CATEGORY_MAPPING),
        (_DERIVED_BUSINESS_METRIC.operation, _DERIVED_BUSINESS_METRIC),
        (_STRING_NORMALIZATION.operation, _STRING_NORMALIZATION),
        (_MISSING_VALUE_HANDLING.operation, _MISSING_VALUE_HANDLING),
        (_NUMERIC_OUTLIER_HANDLING.operation, _NUMERIC_OUTLIER_HANDLING),
        (_NUMERIC_SCALING.operation, _NUMERIC_SCALING),
    )
)


_MAX_ORCHESTRATION_STEPS = 32
_MAX_ORCHESTRATION_PREREQUISITES = 64
_MAX_ORCHESTRATION_AFFECTED_COLUMNS = 128
_ORCHESTRATION_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_AGGREGATE_SUMMARY_KEYS = frozenset(
    {
        "rows_before",
        "rows_retained",
        "rows_filtered",
        "rows_requiring_review",
        "rows_total",
        "rows_mapped",
        "rows_unmapped_preserved_or_replaced",
        "rows_null_preserved_or_replaced",
        "rows_after",
        "null_result_count",
        "rows_checked",
        "affected_count",
        "review_count",
        "denominator_count",
    }
)


class PreprocessingOrchestrationError(TransformationRegistryError):
    """Raised when an orchestration plan cannot be validated safely."""


@dataclass(frozen=True)
class PreprocessingStep:
    step_id: str
    operation_id: str
    configuration: dict | None = None
    fit_context: dict | None = None


@dataclass(frozen=True)
class PreprocessingApproval:
    step_id: str
    operation_id: str
    configuration: dict | None
    approved_approvals: tuple[str, ...]


@dataclass(frozen=True)
class PreprocessingStepResult:
    step_id: str
    operation_id: str
    operation_version: str
    configuration_sha256: str
    status: Literal["completed", "failed", "not_executed"]
    rows_before: int | None
    rows_after: int | None
    columns_before: int | None
    columns_after: int | None
    affected_columns: tuple[str, ...]
    summary_metadata: dict | list | None = None
    input_fingerprint: dict | None = None
    output_fingerprint: dict | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class PreprocessingResult:
    status: Literal["completed", "failed"]
    final_table: pa.Table | None
    steps: tuple[PreprocessingStepResult, ...]
    execution_order: tuple[str, ...]
    completed_steps: tuple[str, ...]
    unexecuted_steps: tuple[str, ...]
    failure_step_id: str | None = None
    error_description: str | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class _PreparedPreprocessingStep:
    step: PreprocessingStep
    operation: TransformationOperation
    executor: TransformationExecutor
    configuration: dict
    fit_context: dict | None
    affected_columns: tuple[str, ...]


def _validate_orchestration_table(data: Any) -> pa.Table:
    if not isinstance(data, pa.Table):
        raise PreprocessingOrchestrationError(
            "Preprocessing orchestration requires a PyArrow Table."
        )
    names = data.schema.names
    if (
        data.num_rows > _MAX_TRANSFORMATION_ROWS
        or data.num_columns > _MAX_TRANSFORMATION_COLUMNS
        or data.nbytes > _MAX_TRANSFORMATION_INPUT_BYTES
    ):
        raise PreprocessingOrchestrationError(
            "Preprocessing table exceeds the global row, column, or byte limit."
        )
    if any(not name.strip() for name in names) or len(names) != len(set(names)):
        raise PreprocessingOrchestrationError(
            "Preprocessing input must have unique, non-empty column names."
        )
    return data


def _orchestration_tokens(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise PreprocessingOrchestrationError(
            f"Preprocessing {field_name} must be a collection of identifiers."
        )
    try:
        supplied = tuple(islice(values, _MAX_ORCHESTRATION_PREREQUISITES + 1))
    except Exception as error:
        raise PreprocessingOrchestrationError(
            f"Preprocessing {field_name} must be a collection of identifiers."
        ) from error
    if len(supplied) > _MAX_ORCHESTRATION_PREREQUISITES:
        raise PreprocessingOrchestrationError(
            f"Preprocessing {field_name} cannot contain more than "
            f"{_MAX_ORCHESTRATION_PREREQUISITES} identifiers."
        )
    tokens = _validated_tokens(supplied, field_name)
    if len(supplied) != len(tokens):
        raise PreprocessingOrchestrationError(
            f"Preprocessing {field_name} must not contain duplicate identifiers."
        )
    return tuple(sorted(tokens))


def _affected_columns(configuration: dict) -> tuple[str, ...]:
    columns = set(configuration.get("columns", ()))
    for key in ("column", "left_column", "right_column", "output_column"):
        value = configuration.get(key)
        if isinstance(value, str):
            columns.add(value)
    for key in ("predicates", "rules"):
        for item in configuration.get(key, ()):
            if isinstance(item, dict):
                for column_key in ("column", "other_column"):
                    value = item.get(column_key)
                    if isinstance(value, str):
                        columns.add(value)
    if len(columns) > _MAX_ORCHESTRATION_AFFECTED_COLUMNS:
        raise PreprocessingOrchestrationError(
            "A preprocessing step exceeds the affected-column metadata limit."
        )
    return tuple(sorted(columns))


def _safe_step_error(error: Exception, phase: str, operation_id: str) -> str:
    if isinstance(error, TransformationRegistryError):
        message = "".join(
            character
            for character in str(error)
            if character >= " " and character not in "\x7f"
        )
        if len(message) > 512:
            message = message[:509] + "..."
        return f"{operation_id} failed during {phase}: {message}"
    error_type = type(error).__name__
    if not error_type.isidentifier():
        error_type = "ExecutorError"
    return f"{operation_id} failed during {phase} ({error_type})."


def _read_transformation_lineage(data: pa.Table) -> list[dict] | None:
    encoded = (data.schema.metadata or {}).get(
        b"insightos.transformation_lineage"
    )
    if encoded is None:
        return None
    if not isinstance(encoded, bytes) or len(encoded) > 16 * 1024:
        raise TransformationRegistryError(
            "Transformation lineage metadata is invalid or exceeds its size limit."
        )
    try:
        lineage = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransformationRegistryError(
            "Transformation lineage metadata is invalid."
        ) from error
    if (
        not isinstance(lineage, list)
        or len(lineage) > 32
        or any(not isinstance(entry, dict) for entry in lineage)
    ):
        raise TransformationRegistryError(
            "Transformation lineage metadata is invalid or exceeds its operation limit."
        )
    return lineage


def _bounded_summary_value(value: Any, budget: list[int], depth: int = 0) -> Any:
    if depth > 4 or budget[0] <= 0:
        return None
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            if (
                budget[0] <= 0
                or not isinstance(key, str)
                or key not in _SAFE_AGGREGATE_SUMMARY_KEYS
            ):
                continue
            budget[0] -= 1
            item = _bounded_summary_value(value[key], budget, depth + 1)
            if item is not None:
                result[key] = item
        return result or None
    if isinstance(value, list):
        result = []
        for item in value[:32]:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            bounded = _bounded_summary_value(item, budget, depth + 1)
            if bounded is not None:
                result.append(bounded)
        return result or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and abs(value) <= 2**63:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _step_summary_metadata(
    input_table: pa.Table,
    output_table: pa.Table,
    operation: TransformationOperation,
    configuration: dict,
) -> dict | list | None:
    if not operation.changes_metadata:
        return None
    input_lineage = _read_transformation_lineage(input_table)
    output_lineage = _read_transformation_lineage(output_table)
    if (
        output_lineage is None
        or len(output_lineage)
        != (0 if input_lineage is None else len(input_lineage)) + 1
    ):
        return None
    entry = output_lineage[-1]
    if (
        entry.get("operation_id") != operation.operation_id
        or entry.get("version") != operation.version
        or entry.get("configuration_sha256")
        != _configuration_fingerprint(configuration)
        or not isinstance(entry.get("summary"), (dict, list))
    ):
        return None
    summary = _bounded_summary_value(entry["summary"], [32])
    if not isinstance(summary, (dict, list)):
        return None
    if len(
        json.dumps(
            summary,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) > 4096:
        return None
    return summary


def _arrow_provenance_reference(
    fingerprint: dict,
    rows: int,
    columns: int,
) -> dict:
    return {
        "kind": "arrow_table",
        "sha256": fingerprint["sha256"],
        "size_bytes": fingerprint["size_bytes"],
        "schema_sha256": fingerprint["schema_sha256"],
        "row_count": rows,
        "column_count": columns,
    }


def _orchestration_provenance(
    *,
    plan_sha256: str,
    status: str,
    input_table: pa.Table,
    input_fingerprint: dict,
    output_table: pa.Table | None,
    output_fingerprint: dict | None,
    step_results: list[PreprocessingStepResult],
) -> dict:
    return build_provenance(
        operation_type="preprocessing_orchestration",
        operation_version="1",
        configuration_sha256=plan_sha256,
        status=status,
        inputs=[
            _arrow_provenance_reference(
                input_fingerprint,
                input_table.num_rows,
                input_table.num_columns,
            )
        ],
        outputs=(
            [
                _arrow_provenance_reference(
                    output_fingerprint,
                    output_table.num_rows,
                    output_table.num_columns,
                )
            ]
            if output_table is not None and output_fingerprint is not None
            else []
        ),
        verification_status="unverifiable",
        checks=[
            {"name": "in_memory_arrow_fingerprints", "status": "verified"}
        ],
        limitations=[
            "Preprocessing output remains in memory; no independent artifact read-back or persisted lineage exists."
        ],
        parent_operation_ids=[
            result.provenance["operation_id"]
            for result in step_results
            if result.provenance is not None
        ],
    )


def orchestrate_preprocessing(
    data: pa.Table,
    steps: list[PreprocessingStep] | tuple[PreprocessingStep, ...],
    *,
    approvals: list[PreprocessingApproval] | tuple[PreprocessingApproval, ...] = (),
    available_prerequisites: Iterable[str] = (),
    registry: TransformationRegistry = DEFAULT_TRANSFORMATION_REGISTRY,
) -> PreprocessingResult:
    """Validate a complete ordered plan, then execute it without fitting on evaluation data."""
    _validate_orchestration_table(data)
    if not isinstance(steps, (list, tuple)):
        raise PreprocessingOrchestrationError(
            "Preprocessing steps must be an ordered list or tuple."
        )
    if len(steps) > _MAX_ORCHESTRATION_STEPS:
        raise PreprocessingOrchestrationError(
            f"A preprocessing plan cannot contain more than {_MAX_ORCHESTRATION_STEPS} steps."
        )
    if not isinstance(approvals, (list, tuple)) or len(approvals) > _MAX_ORCHESTRATION_STEPS:
        raise PreprocessingOrchestrationError(
            "Preprocessing approvals must be a bounded list or tuple."
        )
    if not isinstance(registry, TransformationRegistry):
        raise PreprocessingOrchestrationError(
            "Preprocessing registry is invalid."
        )

    available = set(
        _orchestration_tokens(
            available_prerequisites,
            "available prerequisites",
        )
    )
    available.add("pyarrow_table")
    operations = {
        operation.operation_id: operation
        for operation in registry.list_operations()
    }
    operation_steps: dict[
        str,
        tuple[PreprocessingStep, TransformationOperation, dict, dict | None],
    ] = {}
    step_ids = set()
    for step in steps:
        if (
            not isinstance(step, PreprocessingStep)
            or not isinstance(step.step_id, str)
            or _ORCHESTRATION_STEP_ID.fullmatch(step.step_id) is None
            or not isinstance(step.operation_id, str)
            or len(step.operation_id) > 480
            or _OPERATION_ID.fullmatch(step.operation_id) is None
        ):
            raise PreprocessingOrchestrationError(
                "Each preprocessing step needs a bounded step ID and operation ID."
            )
        if step.step_id in step_ids:
            raise PreprocessingOrchestrationError(
                "Preprocessing step IDs must be unique."
            )
        step_ids.add(step.step_id)
        operation = operations.get(step.operation_id)
        if operation is None:
            raise PreprocessingOrchestrationError(
                f"Unknown transformation operation {step.operation_id!r}."
            )
        try:
            configuration = _operation_configuration(
                operation,
                copy.deepcopy(step.configuration),
            )
        except Exception as error:
            raise PreprocessingOrchestrationError(
                f"Preprocessing step {step.step_id!r} has an invalid configuration."
            ) from error

        if operation.requires_fit:
            if not isinstance(step.fit_context, dict):
                raise PreprocessingOrchestrationError(
                    f"Preprocessing step {step.step_id!r} requires a fitted context from training data."
                )
            try:
                fit_context = copy.deepcopy(step.fit_context)
                _validate_fitted_context_binding(
                    fit_context,
                    operation,
                    configuration,
                )
            except TransformationRegistryError as error:
                raise PreprocessingOrchestrationError(
                    f"Preprocessing step {step.step_id!r} has an incompatible fitted context: {error}"
                ) from error
            except Exception as error:
                raise PreprocessingOrchestrationError(
                    f"Preprocessing step {step.step_id!r} has an invalid fitted context."
                ) from error
        else:
            if step.fit_context is not None:
                raise PreprocessingOrchestrationError(
                    f"Preprocessing step {step.step_id!r} does not accept a fitted context."
                )
            fit_context = None
        operation_steps[step.step_id] = (
            step,
            operation,
            configuration,
            fit_context,
        )

    approval_by_step: dict[str, tuple[set[str], dict]] = {}
    for approval in approvals:
        if (
            not isinstance(approval, PreprocessingApproval)
            or not isinstance(approval.step_id, str)
            or not isinstance(approval.operation_id, str)
        ):
            raise PreprocessingOrchestrationError(
                "Each preprocessing approval must identify a plan step and operation."
            )
        if approval.step_id in approval_by_step:
            raise PreprocessingOrchestrationError(
                "A preprocessing step cannot have duplicate approval records."
            )
        planned = operation_steps.get(approval.step_id)
        if planned is None:
            raise PreprocessingOrchestrationError(
                "A preprocessing approval refers to an unknown step."
            )
        step, operation, configuration, _ = planned
        if approval.operation_id != step.operation_id:
            raise PreprocessingOrchestrationError(
                f"Approval for step {step.step_id!r} names a different operation."
            )
        try:
            approved_configuration = _operation_configuration(
                operation,
                copy.deepcopy(approval.configuration),
            )
            tokens = _orchestration_tokens(
                approval.approved_approvals,
                "approved approvals",
            )
        except Exception as error:
            raise PreprocessingOrchestrationError(
                f"Approval for step {step.step_id!r} is invalid."
            ) from error
        if approved_configuration != configuration:
            raise PreprocessingOrchestrationError(
                f"Approval for step {step.step_id!r} does not match its effective configuration."
            )
        if set(tokens) != set(operation.required_approvals):
            raise PreprocessingOrchestrationError(
                f"Approval for step {step.step_id!r} must exactly match the operation's required approval tokens."
            )
        approval_by_step[step.step_id] = (set(tokens), approved_configuration)

    prepared_steps = []
    for step in steps:
        _, operation, configuration, fit_context = operation_steps[step.step_id]
        approval = approval_by_step.get(step.step_id)
        approved_tokens = () if approval is None else tuple(sorted(approval[0]))
        missing_approval = set(operation.required_approvals) - set(approved_tokens)
        if missing_approval:
            raise PreprocessingOrchestrationError(
                f"Approval is required for preprocessing step {step.step_id!r}."
            )
        try:
            executor = registry.resolve_executor(
                operation.operation_id,
                configuration=configuration,
                available_prerequisites=available,
                approved_approvals=approved_tokens,
            )
        except TransformationRegistryError as error:
            raise PreprocessingOrchestrationError(
                f"Preprocessing step {step.step_id!r} cannot be dispatched: {error}"
            ) from error
        prepared_steps.append(
            _PreparedPreprocessingStep(
                step=step,
                operation=operation,
                executor=executor,
                configuration=configuration,
                fit_context=fit_context,
                affected_columns=_affected_columns(configuration),
            )
        )

    execution_order = tuple(step.step.step_id for step in prepared_steps)
    input_fingerprint = arrow_table_fingerprint(data)
    plan_sha256 = canonical_sha256(
        [
            {
                "step_id": prepared.step.step_id,
                "operation_id": prepared.operation.operation_id,
                "operation_version": prepared.operation.version,
                "configuration_sha256": _configuration_fingerprint(
                    prepared.configuration
                ),
            }
            for prepared in prepared_steps
        ]
    )
    if not prepared_steps:
        return PreprocessingResult(
            status="completed",
            final_table=data,
            steps=(),
            execution_order=(),
            completed_steps=(),
            unexecuted_steps=(),
            provenance=_orchestration_provenance(
                plan_sha256=plan_sha256,
                status="completed",
                input_table=data,
                input_fingerprint=input_fingerprint,
                output_table=data,
                output_fingerprint=input_fingerprint,
                step_results=[],
            ),
        )

    working = data
    working_fingerprint = input_fingerprint
    results = []
    completed_steps = []
    previous_operation_id = None
    for index, prepared in enumerate(prepared_steps):
        step = prepared.step
        before_rows = working.num_rows
        before_columns = working.num_columns
        phase = "fit"
        try:
            _validate_table(working, prepared.operation)
            if prepared.operation.requires_fit:
                context = prepared.fit_context
            else:
                context = prepared.executor.fit(
                    working,
                    configuration=prepared.configuration,
                )
            phase = "transform"
            output = prepared.executor.transform(
                working,
                configuration=prepared.configuration,
                context=context,
            )
            phase = "output validation"
            if not isinstance(output, pa.Table):
                raise TransformationRegistryError(
                    "Transformation output must be a PyArrow Table."
                )
            _validate_table(output, prepared.operation)
            _validate_orchestration_table(output)
            output_fingerprint = arrow_table_fingerprint(output)
            summary_metadata = _step_summary_metadata(
                working,
                output,
                prepared.operation,
                prepared.configuration,
            )
        except Exception as error:
            failed_provenance = build_provenance(
                operation_type=f"transformation:{prepared.operation.operation_id}",
                operation_version=prepared.operation.version,
                configuration_sha256=_configuration_fingerprint(
                    prepared.configuration
                ),
                status="failed",
                inputs=[
                    _arrow_provenance_reference(
                        working_fingerprint,
                        working.num_rows,
                        working.num_columns,
                    )
                ],
                outputs=[],
                verification_status="unverifiable",
                checks=[],
                limitations=[
                    "The failed transformation produced no published output table."
                ],
                parent_operation_ids=(
                    [previous_operation_id]
                    if previous_operation_id is not None
                    else []
                ),
            )
            results.append(
                PreprocessingStepResult(
                    step_id=step.step_id,
                    operation_id=step.operation_id,
                    operation_version=prepared.operation.version,
                    configuration_sha256=_configuration_fingerprint(
                        prepared.configuration
                    ),
                    status="failed",
                    rows_before=before_rows,
                    rows_after=None,
                    columns_before=before_columns,
                    columns_after=None,
                    affected_columns=prepared.affected_columns,
                    input_fingerprint=working_fingerprint,
                    provenance=failed_provenance,
                )
            )
            unexecuted = prepared_steps[index + 1 :]
            results.extend(
                PreprocessingStepResult(
                    step_id=item.step.step_id,
                    operation_id=item.step.operation_id,
                    operation_version=item.operation.version,
                    configuration_sha256=_configuration_fingerprint(
                        item.configuration
                    ),
                    status="not_executed",
                    rows_before=None,
                    rows_after=None,
                    columns_before=None,
                    columns_after=None,
                    affected_columns=item.affected_columns,
                )
                for item in unexecuted
            )
            return PreprocessingResult(
                status="failed",
                final_table=None,
                steps=tuple(results),
                execution_order=execution_order,
                completed_steps=tuple(completed_steps),
                unexecuted_steps=tuple(item.step.step_id for item in unexecuted),
                failure_step_id=step.step_id,
                error_description=_safe_step_error(
                    error,
                    phase,
                    step.operation_id,
                ),
                provenance=_orchestration_provenance(
                    plan_sha256=plan_sha256,
                    status="failed",
                    input_table=data,
                    input_fingerprint=input_fingerprint,
                    output_table=None,
                    output_fingerprint=None,
                    step_results=results,
                ),
            )

        step_provenance = build_provenance(
            operation_type=f"transformation:{prepared.operation.operation_id}",
            operation_version=prepared.operation.version,
            configuration_sha256=_configuration_fingerprint(
                prepared.configuration
            ),
            status="completed",
            inputs=[
                _arrow_provenance_reference(
                    working_fingerprint,
                    working.num_rows,
                    working.num_columns,
                )
            ],
            outputs=[
                _arrow_provenance_reference(
                    output_fingerprint,
                    output.num_rows,
                    output.num_columns,
                )
            ],
            verification_status="unverifiable",
            checks=[
                {"name": "input_table_fingerprint", "status": "verified"},
                {"name": "output_table_fingerprint", "status": "verified"},
                {"name": "output_schema", "status": "verified"},
            ],
            limitations=[
                "The Arrow output is in memory and has no independent persisted-artifact verification."
            ],
            parent_operation_ids=(
                [previous_operation_id]
                if previous_operation_id is not None
                else []
            ),
        )
        results.append(
            PreprocessingStepResult(
                step_id=step.step_id,
                operation_id=step.operation_id,
                operation_version=prepared.operation.version,
                configuration_sha256=_configuration_fingerprint(
                    prepared.configuration
                ),
                status="completed",
                rows_before=before_rows,
                rows_after=output.num_rows,
                columns_before=before_columns,
                columns_after=output.num_columns,
                affected_columns=prepared.affected_columns,
                summary_metadata=summary_metadata,
                input_fingerprint=working_fingerprint,
                output_fingerprint=output_fingerprint,
                provenance=step_provenance,
            )
        )
        completed_steps.append(step.step_id)
        working = output
        working_fingerprint = output_fingerprint
        previous_operation_id = step_provenance["operation_id"]

    return PreprocessingResult(
        status="completed",
        final_table=working,
        steps=tuple(results),
        execution_order=execution_order,
        completed_steps=tuple(completed_steps),
        unexecuted_steps=(),
        provenance=_orchestration_provenance(
            plan_sha256=plan_sha256,
            status="completed",
            input_table=data,
            input_fingerprint=input_fingerprint,
            output_table=working,
            output_fingerprint=working_fingerprint,
            step_results=results,
        ),
    )
