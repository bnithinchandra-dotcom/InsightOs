from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from threading import Event, RLock
from time import monotonic
from typing import Any, Callable, Generic, Literal, TypeVar


logger = logging.getLogger(__name__)

ExecutionKind = Literal["browser", "local_runtime", "backend"]
DataLocality = Literal["browser", "local_runtime", "backend", "any"]
DataClassification = Literal["public", "internal", "sensitive", "local_only"]
ExecutionStatus = Literal[
    "completed",
    "failed",
    "unavailable",
    "cancelled",
    "timed_out",
]
MAX_TASK_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RESULT_BYTES = 256 * 1024
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True)
class ExecutionTarget:
    target_id: str
    kind: ExecutionKind
    capabilities: frozenset[str]
    data_classifications: frozenset[DataClassification]
    available: bool = True
    memory_bytes: int | None = None
    supports_cancellation: bool = False
    supports_timeout: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.target_id, str)
            or not self.target_id
            or self.kind
            not in {
                "browser",
                "local_runtime",
                "backend",
            }
        ):
            raise ValueError("Execution targets need a valid identifier and kind.")
        if not self.capabilities or any(
            not isinstance(item, str) or not item for item in self.capabilities
        ):
            raise ValueError("Execution targets need an identifier and capabilities.")
        if not self.data_classifications or any(
            item not in {"public", "internal", "sensitive", "local_only"}
            for item in self.data_classifications
        ):
            raise ValueError("Execution targets need valid data classifications.")
        if not isinstance(self.available, bool) or not isinstance(
            self.supports_cancellation, bool
        ) or not isinstance(self.supports_timeout, bool):
            raise ValueError("Execution target flags must be boolean.")
        if self.memory_bytes is not None and (
            isinstance(self.memory_bytes, bool)
            or not isinstance(self.memory_bytes, int)
            or self.memory_bytes < 0
        ):
            raise ValueError("Target memory must be a non-negative integer.")


@dataclass(frozen=True)
class TaskRequirements:
    required_capabilities: tuple[str, ...]
    data_locality: DataLocality = "any"
    data_classification: DataClassification = "internal"
    allowed_targets: tuple[ExecutionKind, ...] = (
        "browser",
        "local_runtime",
        "backend",
    )
    prefer_local: bool = True
    allow_data_transfer: bool = False
    minimum_memory_bytes: int | None = None
    timeout_seconds: float | None = None
    require_cancellation: bool = False
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES

    def __post_init__(self) -> None:
        if self.data_locality not in {"browser", "local_runtime", "backend", "any"}:
            raise ValueError("Task data locality is invalid.")
        if self.data_classification not in {
            "public",
            "internal",
            "sensitive",
            "local_only",
        }:
            raise ValueError("Task data classification is invalid.")
        if (
            not isinstance(self.required_capabilities, tuple)
            or not self.required_capabilities
            or any(
                not isinstance(item, str) or not item
                for item in self.required_capabilities
            )
        ):
            raise ValueError("Tasks need at least one named capability.")
        if (
            not isinstance(self.allowed_targets, tuple)
            or not self.allowed_targets
            or any(
                target not in {"browser", "local_runtime", "backend"}
                for target in self.allowed_targets
            )
        ):
            raise ValueError("Tasks need at least one allowed execution target.")
        if self.data_classification == "local_only" and self.allow_data_transfer:
            raise ValueError("Local-only data cannot permit transfer.")
        for name, value in (
            ("prefer_local", self.prefer_local),
            ("allow_data_transfer", self.allow_data_transfer),
            ("require_cancellation", self.require_cancellation),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean.")
        for name, value in (
            ("minimum_memory_bytes", self.minimum_memory_bytes),
            ("max_result_bytes", self.max_result_bytes),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer.")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (float, int))
            or not 0 < self.timeout_seconds <= MAX_TASK_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout_seconds must be greater than 0 and at most {MAX_TASK_TIMEOUT_SECONDS}."
            )


@dataclass(frozen=True)
class PlacementDecision:
    target: ExecutionTarget | None
    rationale: str
    error_code: str | None = None


@dataclass(frozen=True)
class TaskInput(Generic[InputT]):
    task_name: str
    payload: InputT

    def __post_init__(self) -> None:
        if not isinstance(self.task_name, str) or not self.task_name.strip():
            raise ValueError("Tasks need a name.")


class TaskExecutionFailure(Exception):
    def __init__(self, code: str, message: str):
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 64
            or not isinstance(message, str)
            or not message
            or len(message) > 512
        ):
            raise ValueError("Task failures need a bounded code and message.")
        super().__init__(message)
        self.code = code
        self.message = message


class CapabilityRegistry:
    def __init__(self, targets: tuple[ExecutionTarget, ...] = ()):
        self._targets: dict[str, ExecutionTarget] = {}
        self._lock = RLock()
        for target in targets:
            self.register(target)

    def register(self, target: ExecutionTarget) -> None:
        if not isinstance(target, ExecutionTarget):
            raise TypeError("Only validated execution targets can be registered.")
        with self._lock:
            if target.target_id in self._targets:
                raise ValueError("Execution target identifiers must be unique.")
            self._targets[target.target_id] = target

    def targets(self) -> tuple[ExecutionTarget, ...]:
        with self._lock:
            return tuple(self._targets[key] for key in sorted(self._targets))


def select_execution_target(
    requirements: TaskRequirements,
    targets: tuple[ExecutionTarget, ...],
) -> PlacementDecision:
    compatible: list[tuple[tuple[int, int, str], ExecutionTarget]] = []
    for target in targets:
        if not target.available or target.kind not in requirements.allowed_targets:
            continue
        if not set(requirements.required_capabilities).issubset(target.capabilities):
            continue
        if requirements.data_classification not in target.data_classifications:
            continue
        if requirements.data_classification == "local_only" and target.kind == "backend":
            continue
        if (
            requirements.data_locality != "any"
            and target.kind != requirements.data_locality
            and not requirements.allow_data_transfer
        ):
            continue
        if requirements.minimum_memory_bytes is not None and (
            target.memory_bytes is None
            or target.memory_bytes < requirements.minimum_memory_bytes
        ):
            continue
        if requirements.timeout_seconds is not None and not target.supports_timeout:
            continue
        if requirements.require_cancellation and not target.supports_cancellation:
            continue

        locality_rank = 0
        if requirements.data_locality != "any":
            locality_rank = 0 if target.kind == requirements.data_locality else 1
        preference_rank = 0
        if requirements.prefer_local:
            preference_rank = 0 if target.kind in {"browser", "local_runtime"} else 1
        compatible.append(
            ((locality_rank, preference_rank, target.target_id), target)
        )

    if not compatible:
        return PlacementDecision(
            target=None,
            rationale=(
                "No available target satisfies the task capabilities, locality, "
                "privacy, resource, and timeout constraints."
            ),
            error_code="no_execution_target",
        )

    compatible.sort(key=lambda candidate: candidate[0])
    selected = compatible[0][1]
    reasons = [
        f"{selected.target_id} supports the required capabilities",
        "the target is available",
        f"the target permits {requirements.data_classification} data",
    ]
    if requirements.data_locality != "any":
        reasons.append(
            "the target matches data locality"
            if selected.kind == requirements.data_locality
            else "data transfer is explicitly permitted"
        )
    if requirements.minimum_memory_bytes is not None:
        reasons.append("the measured target memory meets the minimum")
    if requirements.timeout_seconds is not None:
        reasons.append("the target supports enforced timeouts")
    if requirements.require_cancellation:
        reasons.append("the target supports cancellation")
    return PlacementDecision(target=selected, rationale="; ".join(reasons) + ".")


class ExecutionCancelled(Exception):
    pass


class ExecutionContext:
    def __init__(
        self,
        *,
        cancellation: Event,
        deadline: float | None,
    ):
        self._cancellation = cancellation
        self._deadline = deadline
        self.progress = 0

    def report_progress(self, percentage: int) -> None:
        if isinstance(percentage, bool) or not isinstance(percentage, int):
            raise ValueError("Execution progress must be a whole percentage.")
        if percentage < self.progress or percentage > 100:
            raise ValueError("Execution progress must be monotonic and at most 100.")
        self.progress = percentage
        self.checkpoint()

    def checkpoint(self) -> None:
        if self._cancellation.is_set():
            raise ExecutionCancelled()
        if self._deadline is not None and monotonic() >= self._deadline:
            raise TimeoutError()


@dataclass(frozen=True)
class TaskExecutionResult(Generic[OutputT]):
    status: ExecutionStatus
    target_id: str | None
    rationale: str
    progress: int
    result: OutputT | None = None
    error_code: str | None = None
    error_message: str | None = None


def run_task(
    *,
    task: TaskInput[InputT],
    requirements: TaskRequirements,
    registry: CapabilityRegistry,
    operation: Callable[[ExecutionContext, InputT], OutputT],
    result_validator: Callable[[OutputT], bool],
    result_serializer: Callable[[OutputT], Any] | None = None,
    cancellation: Event | None = None,
) -> TaskExecutionResult[OutputT]:
    decision = select_execution_target(requirements, registry.targets())
    if decision.target is None:
        return TaskExecutionResult(
            status="unavailable",
            target_id=None,
            rationale=decision.rationale,
            progress=0,
            error_code=decision.error_code,
            error_message=decision.rationale,
        )

    deadline = (
        monotonic() + requirements.timeout_seconds
        if requirements.timeout_seconds is not None
        else None
    )
    context = ExecutionContext(
        cancellation=cancellation or Event(),
        deadline=deadline,
    )
    try:
        context.checkpoint()
        try:
            result = operation(context, task.payload)
        except ExecutionCancelled:
            raise
        except TimeoutError:
            raise
        except TaskExecutionFailure as error:
            return TaskExecutionResult(
                status="failed",
                target_id=decision.target.target_id,
                rationale=decision.rationale,
                progress=context.progress,
                error_code=error.code,
                error_message=error.message,
            )
        except Exception as error:
            logger.error("Task %s failed (%s)", task.task_name, type(error).__name__)
            return TaskExecutionResult(
                status="failed",
                target_id=decision.target.target_id,
                rationale=decision.rationale,
                progress=context.progress,
                error_code="task_execution_failed",
                error_message="The task could not be completed.",
            )
        context.checkpoint()
        if not result_validator(result):
            raise ValueError("The execution result did not match its contract.")
        serialized_result = (
            result_serializer(result) if result_serializer is not None else result
        )
        result_bytes = json.dumps(
            serialized_result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(result_bytes) > requirements.max_result_bytes:
            return TaskExecutionResult(
                status="failed",
                target_id=decision.target.target_id,
                rationale=decision.rationale,
                progress=context.progress,
                error_code="result_too_large",
                error_message="The task result exceeded its safe output limit.",
            )
    except ExecutionCancelled:
        return TaskExecutionResult(
            status="cancelled",
            target_id=decision.target.target_id,
            rationale=decision.rationale,
            progress=context.progress,
            error_code="execution_cancelled",
            error_message="The task was cancelled.",
        )
    except TimeoutError:
        return TaskExecutionResult(
            status="timed_out",
            target_id=decision.target.target_id,
            rationale=decision.rationale,
            progress=context.progress,
            error_code="execution_timed_out",
            error_message="The task exceeded its execution time limit.",
        )
    except (TypeError, ValueError, OverflowError):
        return TaskExecutionResult(
            status="failed",
            target_id=decision.target.target_id,
            rationale=decision.rationale,
            progress=context.progress,
            error_code="invalid_execution_result",
            error_message="The task returned an invalid or oversized result.",
        )
    except Exception as error:
        logger.error("Task %s failed (%s)", task.task_name, type(error).__name__)
        return TaskExecutionResult(
            status="failed",
            target_id=decision.target.target_id,
            rationale=decision.rationale,
            progress=context.progress,
            error_code="task_execution_failed",
            error_message="The task could not be completed.",
        )

    return TaskExecutionResult(
        status="completed",
        target_id=decision.target.target_id,
        rationale=decision.rationale,
        progress=100,
        result=result,
    )


def backend_agent_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        (
            ExecutionTarget(
                target_id="backend",
                kind="backend",
                capabilities=frozenset({"readonly_agent_tool"}),
                data_classifications=frozenset({"internal", "sensitive"}),
            ),
        )
    )


def backend_eda_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        (
            ExecutionTarget(
                target_id="backend",
                kind="backend",
                capabilities=frozenset({"deterministic_eda"}),
                data_classifications=frozenset({"sensitive"}),
            ),
        )
    )
