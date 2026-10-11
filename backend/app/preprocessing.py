import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

import pyarrow as pa
import pyarrow.compute as pc

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
        or not any(
            (
                operation.changes_rows,
                operation.changes_columns,
                operation.changes_values,
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


def _object_schema(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": [],
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
    if len(resolved.get("constant_string", "")) > _MAX_TEXT_LENGTH:
        raise TransformationRegistryError(
            "String imputation constants must be no longer than 512 characters."
        )
    return resolved


def _validate_table(
    data: Any,
    operation: TransformationOperation,
) -> pa.Table:
    if not isinstance(data, pa.Table):
        raise TransformationRegistryError(
            "DA transformations require a PyArrow Table."
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
    if not isinstance(context, dict):
        raise TransformationRegistryError(
            "Transformation must be fitted before transform is called."
        )
    if (
        context.get("operation_id") != operation.operation_id
        or context.get("version") != operation.version
        or context.get("configuration_sha256")
        != _configuration_fingerprint(configuration)
        or context.get("schema") != _schema_fingerprint(data)
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
    ) -> list[str | bool]:
        observed = []
        for value in values.to_pylist():
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


_TRANSFORMATION_RESOURCE_LIMITS = {
    "max_input_bytes": _MAX_TRANSFORMATION_INPUT_BYTES,
    "max_rows": _MAX_TRANSFORMATION_ROWS,
    "max_columns": _MAX_TRANSFORMATION_COLUMNS,
}
_TRANSFORMATION_PREREQUISITES = (
    "pyarrow_table",
    "explicit_column_selection",
)


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

DEFAULT_TRANSFORMATION_REGISTRY = TransformationRegistry(
    (
        (_STRING_NORMALIZATION.operation, _STRING_NORMALIZATION),
        (_MISSING_VALUE_HANDLING.operation, _MISSING_VALUE_HANDLING),
        (_NUMERIC_OUTLIER_HANDLING.operation, _NUMERIC_OUTLIER_HANDLING),
        (_NUMERIC_SCALING.operation, _NUMERIC_SCALING),
    )
)
