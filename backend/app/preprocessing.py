import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

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
    ) -> None: ...

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


DEFAULT_TRANSFORMATION_REGISTRY = TransformationRegistry()
