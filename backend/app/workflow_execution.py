from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import update

from app.models.workflow import (
    WorkflowExecution,
    WorkflowTask,
    WorkflowTaskDependency,
)


logger = logging.getLogger(__name__)
MAX_WORKFLOW_TASKS = 32
MAX_TASK_RETRIES = 3
MAX_TASK_TIMEOUT_SECONDS = 300
MAX_TASK_RESULT_BYTES = 32 * 1024
MAX_PLAN_BYTES = 128 * 1024
_TASK_STATUSES = {
    "pending",
    "running",
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
    "skipped",
    "unsupported",
}
_RUNNABLE_DEPENDENCY_STATUSES = {"completed"}


class WorkflowPlanError(ValueError):
    pass


class WorkflowTaskExecutionError(RuntimeError):
    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def validate_task_plan(plan: list[dict[str, Any]]) -> list[str]:
    if not isinstance(plan, list) or not plan or len(plan) > MAX_WORKFLOW_TASKS:
        raise WorkflowPlanError(
            f"A workflow plan must contain 1 to {MAX_WORKFLOW_TASKS} tasks."
        )
    try:
        plan_bytes = _json_size(plan)
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise WorkflowPlanError("Workflow plan must contain finite JSON values.") from error
    if plan_bytes > MAX_PLAN_BYTES:
        raise WorkflowPlanError("The workflow plan exceeds its storage limit.")
    keys: list[str] = []
    dependencies: dict[str, list[str]] = {}
    for item in plan:
        if not isinstance(item, dict):
            raise WorkflowPlanError("Every workflow task must be an object.")
        allowed_fields = {
            "task_id",
            "agent",
            "operation",
            "depends_on",
            "status",
            "dataset_id",
            "file_id",
            "source_fingerprint",
            "input_references",
            "output_references",
            "summary",
            "sequence_hint",
            "idempotent",
            "max_retries",
            "timeout_seconds",
        }
        if set(item) - allowed_fields:
            raise WorkflowPlanError("Workflow task contains unsupported fields.")
        key = item.get("task_id")
        agent = item.get("agent")
        operation = item.get("operation")
        depends_on = item.get("depends_on")
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 80
            or key in dependencies
        ):
            raise WorkflowPlanError("Workflow task identifiers must be unique and bounded.")
        if not isinstance(agent, str) or not agent or len(agent) > 64:
            raise WorkflowPlanError(f"Task {key!r} needs a valid agent identity.")
        if not isinstance(operation, str) or not operation or len(operation) > 96:
            raise WorkflowPlanError(f"Task {key!r} needs a valid operation name.")
        if not isinstance(depends_on, list) or len(depends_on) > 8 or any(
            not isinstance(dep, str) for dep in depends_on
        ):
            raise WorkflowPlanError(f"Task {key!r} has invalid dependencies.")
        if len(set(depends_on)) != len(depends_on):
            raise WorkflowPlanError(f"Task {key!r} repeats a dependency.")
        status = item.get("status", "pending")
        if status not in _TASK_STATUSES:
            raise WorkflowPlanError(f"Task {key!r} has an invalid status.")
        for field in ("dataset_id", "file_id"):
            value = item.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise WorkflowPlanError(f"Task {key!r} has an invalid {field}.")
        fingerprint = item.get("source_fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or len(fingerprint) > 128
        ):
            raise WorkflowPlanError(f"Task {key!r} has an invalid source fingerprint.")
        for field in ("input_references", "output_references"):
            references = item.get(field, [])
            try:
                references_size = _json_size(references)
            except (TypeError, ValueError, UnicodeError, OverflowError):
                references_size = 8193
            if not isinstance(references, list) or len(references) > 32 or references_size > 8192:
                raise WorkflowPlanError(f"Task {key!r} has invalid evidence references.")
        summary = item.get("summary")
        if summary is not None and (
            not isinstance(summary, str) or len(summary) > 1000
        ):
            raise WorkflowPlanError(f"Task {key!r} has an invalid summary.")
        idempotent = item.get("idempotent", False)
        if not isinstance(idempotent, bool):
            raise WorkflowPlanError(f"Task {key!r} has an invalid idempotency flag.")
        max_retries = item.get("max_retries", 0)
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= MAX_TASK_RETRIES
        ):
            raise WorkflowPlanError(f"Task {key!r} has an invalid retry bound.")
        timeout_seconds = item.get("timeout_seconds", 60)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= MAX_TASK_TIMEOUT_SECONDS
        ):
            raise WorkflowPlanError(f"Task {key!r} has an invalid timeout.")
        keys.append(key)
        dependencies[key] = depends_on
    known = set(keys)
    for key, deps in dependencies.items():
        if any(dep not in known for dep in deps):
            raise WorkflowPlanError(f"Task {key!r} depends on an unknown task.")
        if key in deps:
            raise WorkflowPlanError(f"Task {key!r} cannot depend on itself.")

    pending = set(keys)
    ordered: list[str] = []
    while pending:
        ready = [
            key
            for key in keys
            if key in pending and set(dependencies[key]).issubset(ordered)
        ]
        if not ready:
            raise WorkflowPlanError("Workflow task dependencies contain a cycle.")
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def persist_trace_plan(
    session: Session,
    workflow: WorkflowExecution,
    plan: list[dict[str, Any]],
) -> None:
    ordered_keys = validate_task_plan(plan)
    existing = {
        task.task_key: task
        for task in session.query(WorkflowTask)
        .filter(WorkflowTask.workflow_id == workflow.id)
        .all()
    }
    if existing:
        if set(existing) != set(ordered_keys):
            raise WorkflowPlanError("A persisted workflow plan cannot be replaced.")
        return

    task_ids = {key: uuid4().hex for key in ordered_keys}
    tasks_by_key: dict[str, WorkflowTask] = {}
    for sequence, key in enumerate(ordered_keys, start=1):
        item = next(entry for entry in plan if entry["task_id"] == key)
        status = item.get("status", "pending")
        summary = item.get("summary")
        task = WorkflowTask(
            id=task_ids[key],
            workflow_id=workflow.id,
            task_key=key,
            sequence=sequence,
            agent=item["agent"],
            task_type=item["operation"],
            status=status,
            idempotency_key=hashlib.sha256(
                f"{workflow.id}:{key}".encode("utf-8")
            ).hexdigest(),
            idempotent=item.get("idempotent", False),
            retry_count=0,
            max_retries=item.get("max_retries", 0),
            timeout_seconds=item.get("timeout_seconds", 60),
            dataset_id=item.get("dataset_id"),
            file_id=item.get("file_id"),
            source_fingerprint=item.get("source_fingerprint"),
            input_references=item.get("input_references", []),
            output_references=item.get("output_references", []),
            result_summary=(
                {"summary": summary[:1000]}
                if isinstance(summary, str)
                else None
            ),
            completed_at=_utcnow() if status in {"completed", "partial"} else None,
        )
        session.add(task)
        tasks_by_key[key] = task
    session.flush()
    for item in plan:
        task = tasks_by_key[item["task_id"]]
        for dependency_key in item.get("depends_on", []):
            session.add(
                WorkflowTaskDependency(
                    task_id=task.id,
                    depends_on_task_id=tasks_by_key[dependency_key].id,
                )
            )
    workflow.plan = plan
    workflow.current_step = len(ordered_keys)


def persist_workflow_task(
    session: Session,
    workflow: WorkflowExecution,
    task: dict[str, Any],
) -> WorkflowTask:
    """Persist one executable task before its registered handler is invoked."""
    if not isinstance(task, dict):
        raise WorkflowPlanError("Workflow task must be an object.")
    task = dict(task)
    depends_on = task.get("depends_on", [])
    if (
        not isinstance(depends_on, list)
        or len(depends_on) > 8
        or any(not isinstance(key, str) for key in depends_on)
        or len(set(depends_on)) != len(depends_on)
    ):
        raise WorkflowPlanError("Workflow task dependencies are invalid.")
    dependency_tasks = (
        session.query(WorkflowTask)
        .filter(
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.task_key.in_(depends_on),
        )
        .all()
        if depends_on
        else []
    )
    if {dependency.task_key for dependency in dependency_tasks} != set(depends_on):
        raise WorkflowPlanError(
            "Workflow task dependencies must reference persisted tasks in this workflow."
        )
    input_references = task.get("input_references", [])
    if not isinstance(input_references, list):
        raise WorkflowPlanError("Workflow task inputs must be a list of references.")
    input_references = list(input_references)
    if dependency_tasks:
        if any(
            dependency.status != "completed"
            or not isinstance(dependency.result_summary, dict)
            for dependency in dependency_tasks
        ):
            raise WorkflowPlanError(
                "Workflow task dependencies must be completed before dependent inputs are persisted."
            )
        input_references.append(
            {
                "type": "dependency_outputs",
                "fingerprints": [
                    {
                        "task_id": dependency.task_key,
                        "sha256": hashlib.sha256(
                            json.dumps(
                                dependency.result_summary,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    }
                    for dependency in sorted(
                        dependency_tasks,
                        key=lambda item: item.task_key,
                    )
                ],
            }
        )
    task["input_references"] = input_references
    validated_task = {**task, "depends_on": []}
    validate_task_plan([validated_task])
    task_key = task["task_id"]
    existing = (
        session.query(WorkflowTask)
        .filter(
            WorkflowTask.workflow_id == workflow.id,
            WorkflowTask.task_key == task_key,
        )
        .one_or_none()
    )
    if existing is not None:
        if (
            existing.task_type != task["operation"]
            or existing.input_references != task.get("input_references", [])
            or existing.dataset_id != task.get("dataset_id")
            or existing.file_id != task.get("file_id")
            or existing.source_fingerprint != task.get("source_fingerprint")
        ):
            raise WorkflowPlanError(
                "A workflow task key cannot be reused for different inputs."
            )
        existing_dependencies = {
            dependency.task_key
            for dependency in (
                session.query(WorkflowTask)
                .join(
                    WorkflowTaskDependency,
                    WorkflowTaskDependency.depends_on_task_id == WorkflowTask.id,
                )
                .filter(WorkflowTaskDependency.task_id == existing.id)
                .all()
            )
        }
        if existing_dependencies != set(depends_on):
            raise WorkflowPlanError(
                "A workflow task key cannot be reused with different dependencies."
            )
        return existing
    sequence = (
        session.query(WorkflowTask.sequence)
        .filter(WorkflowTask.workflow_id == workflow.id)
        .order_by(WorkflowTask.sequence.desc())
        .limit(1)
        .scalar()
        or 0
    ) + 1
    task_id = uuid4().hex
    persisted = WorkflowTask(
        id=task_id,
        workflow_id=workflow.id,
        task_key=task_key,
        sequence=sequence,
        agent=task["agent"],
        task_type=task["operation"],
        status="pending",
        idempotency_key=hashlib.sha256(
            f"{workflow.id}:{task_key}".encode("utf-8")
        ).hexdigest(),
        idempotent=task.get("idempotent", False),
        retry_count=0,
        max_retries=task.get("max_retries", 0),
        timeout_seconds=task.get("timeout_seconds", 60),
        dataset_id=task.get("dataset_id"),
        file_id=task.get("file_id"),
        source_fingerprint=task.get("source_fingerprint"),
        input_references=task.get("input_references", []),
        output_references=task.get("output_references", []),
    )
    session.add(persisted)
    session.flush()
    for dependency in dependency_tasks:
        session.add(
            WorkflowTaskDependency(
                task_id=persisted.id,
                depends_on_task_id=dependency.id,
            )
        )
    session.flush()
    workflow.status = "running"
    workflow.completed_at = None
    workflow.error_code = None
    plan = workflow.plan if isinstance(workflow.plan, list) else []
    workflow.plan = plan + [task]
    workflow.current_step = sequence - 1
    return persisted


def plan_from_trace_stages(
    workflow_id: str,
    stages: list[dict[str, Any]],
    source_identity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(stages, list) or len(stages) > MAX_WORKFLOW_TASKS:
        raise WorkflowPlanError("Workflow trace stages are invalid or exceed the task limit.")
    plan: list[dict[str, Any]] = []
    previous_key: str | None = None
    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            raise WorkflowPlanError("Workflow trace stages must be objects.")
        phase = stage.get("phase")
        role = stage.get("role")
        status = stage.get("status")
        if not isinstance(phase, str) or not phase:
            raise WorkflowPlanError("Workflow trace stage has no phase.")
        key = f"{workflow_id}:{index}:{phase}"[:80]
        plan.append(
            {
                "task_id": key,
                "agent": role if isinstance(role, str) and role else "unknown",
                "operation": phase[:96],
                "depends_on": [previous_key] if previous_key else [],
                "status": status,
                "dataset_id": stage.get("dataset_id"),
                "file_id": stage.get("file_id"),
                "source_fingerprint": stage.get(
                    "source_fingerprint",
                    source_identity.get("source_fingerprint")
                    if isinstance(source_identity, dict)
                    else None,
                ),
                "input_references": stage.get("evidence_references", []),
                "output_references": stage.get("evidence_references", []),
                "summary": stage.get("summary"),
                "sequence_hint": index,
            }
        )
        previous_key = key
    return plan


TaskHandler = Callable[[WorkflowTask], dict[str, Any]]
TaskInputValidator = Callable[[Session, WorkflowExecution, WorkflowTask], bool]


def recover_persisted_workflow(
    session: Session,
    workflow_id: str,
    handlers: Mapping[str, TaskHandler],
    input_is_current: TaskInputValidator,
    *,
    stale_before: datetime,
    max_steps: int = MAX_WORKFLOW_TASKS,
) -> tuple[WorkflowExecution, dict[str, list[str]]]:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not (
        1 <= max_steps <= MAX_WORKFLOW_TASKS
    ):
        raise ValueError(f"max_steps must be between 1 and {MAX_WORKFLOW_TASKS}.")
    workflow = (
        session.query(WorkflowExecution)
        .options(selectinload(WorkflowExecution.tasks))
        .filter(WorkflowExecution.id == workflow_id)
        .with_for_update()
        .one_or_none()
    )
    if workflow is None:
        raise LookupError("Workflow not found.")
    tasks = workflow.tasks
    if len(tasks) > max_steps:
        raise WorkflowPlanError("The persisted workflow exceeds the recovery step limit.")

    now = _utcnow()
    report: dict[str, list[str]] = {
        "recovered": [],
        "reused": [],
        "invalidated": [],
        "not_replayed": [],
    }
    task_by_id = {task.id: task for task in tasks}
    dependencies: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for edge in task.dependencies:
            if edge.depends_on_task_id not in task_by_id:
                raise WorkflowPlanError("A task dependency crosses workflow scope.")
            dependencies[task.id].append(edge.depends_on_task_id)
    order = _topological_task_ids(tasks, dependencies)
    input_validity: dict[str, bool] = {}
    for task_id in order:
        task = task_by_id[task_id]
        try:
            input_validity[task_id] = input_is_current(session, workflow, task)
        except Exception as error:
            logger.warning(
                "Workflow recovery input validation failed (%s)",
                type(error).__name__,
            )
            input_validity[task_id] = False

    for task_id in order:
        task = task_by_id[task_id]
        dependency_ids = dependencies[task_id]
        dependencies_valid = all(
            task_by_id[dependency_id].status == "completed"
            and input_validity.get(dependency_id, False)
            for dependency_id in dependency_ids
        )
        if task.status == "completed":
            if not input_validity[task_id] or not dependencies_valid:
                task.status = "blocked"
                task.error_code = "recovery_source_changed"
                task.result_summary = None
                task.output_references = []
                task.completed_at = now
                report["invalidated"].append(task.task_key)
            else:
                report["reused"].append(task.task_key)
            continue

        if task.status == "running":
            if task.updated_at > stale_before:
                continue
            if not task.idempotent:
                task.status = "blocked"
                task.error_code = "interrupted_requires_reconciliation"
                task.completed_at = now
                report["not_replayed"].append(task.task_key)
                continue
            if task.retry_count >= task.max_retries:
                task.status = "blocked"
                task.error_code = "recovery_retry_limit_reached"
                task.completed_at = now
                report["not_replayed"].append(task.task_key)
                continue
            if not input_validity[task_id]:
                task.status = "blocked"
                task.error_code = "recovery_source_changed"
                task.result_summary = None
                task.output_references = []
                task.completed_at = now
                report["invalidated"].append(task.task_key)
                continue
            task.status = "pending"
            task.retry_count += 1
            task.error_code = None
            task.completed_at = None
            report["recovered"].append(task.task_key)
            continue

        if task.status == "failed":
            if not task.idempotent or task.task_type not in handlers:
                report["not_replayed"].append(task.task_key)
                continue
            if task.retry_count >= task.max_retries:
                report["not_replayed"].append(task.task_key)
                continue
            if not input_validity[task_id] or not dependencies_valid:
                task.status = "blocked"
                task.error_code = "recovery_source_changed"
                task.result_summary = None
                task.output_references = []
                task.completed_at = now
                report["invalidated"].append(task.task_key)
                continue
            task.status = "pending"
            task.retry_count += 1
            task.error_code = None
            task.completed_at = None
            report["recovered"].append(task.task_key)
            continue

        if task.status == "pending":
            if not task.idempotent:
                task.status = "blocked"
                task.error_code = "recovery_non_replayable"
                task.completed_at = now
                report["not_replayed"].append(task.task_key)
                continue
            if not input_validity[task_id]:
                task.status = "blocked"
                task.error_code = "recovery_source_changed"
                task.result_summary = None
                task.output_references = []
                task.completed_at = now
                report["invalidated"].append(task.task_key)
                continue
            if task.task_type not in handlers:
                task.status = "unsupported"
                task.error_code = "unsupported_recovery_handler"
                task.completed_at = now
                report["not_replayed"].append(task.task_key)

    workflow.status = "running"
    workflow.completed_at = None
    workflow.error_code = None
    session.commit()

    workflow = run_persisted_workflow(
        session,
        workflow_id,
        handlers,
        max_steps=max_steps,
    )
    return workflow, report


def run_persisted_workflow(
    session: Session,
    workflow_id: str,
    handlers: Mapping[str, TaskHandler],
    *,
    max_steps: int = MAX_WORKFLOW_TASKS,
) -> WorkflowExecution:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not (
        1 <= max_steps <= MAX_WORKFLOW_TASKS
    ):
        raise ValueError(f"max_steps must be between 1 and {MAX_WORKFLOW_TASKS}.")
    workflow = (
        session.query(WorkflowExecution)
        .options(selectinload(WorkflowExecution.tasks))
        .filter(WorkflowExecution.id == workflow_id)
        .with_for_update()
        .one_or_none()
    )
    if workflow is None:
        raise LookupError("Workflow not found.")
    tasks = workflow.tasks
    if len(tasks) > max_steps:
        raise WorkflowPlanError("The persisted workflow exceeds the execution step limit.")
    task_by_id = {task.id: task for task in tasks}
    dependencies: dict[str, list[str]] = {task.id: [] for task in tasks}
    for task in tasks:
        for edge in task.dependencies:
            if edge.depends_on_task_id not in task_by_id:
                raise WorkflowPlanError("A task dependency crosses workflow scope.")
            dependencies[task.id].append(edge.depends_on_task_id)
    order = _topological_task_ids(tasks, dependencies)

    now = _utcnow()
    workflow.status = "running"
    workflow.started_at = workflow.started_at or now
    workflow.completed_at = None
    workflow.error_code = None
    session.flush()

    for task_id in order:
        task = task_by_id[task_id]
        if task.status == "running":
            continue
        if task.status != "pending":
            continue
        dependency_statuses = [task_by_id[item].status for item in dependencies[task_id]]
        if any(status not in _RUNNABLE_DEPENDENCY_STATUSES for status in dependency_statuses):
            if any(status in {"failed", "blocked", "cancelled", "partial", "unsupported"} for status in dependency_statuses):
                task.status = "blocked"
                task.error_code = "dependency_not_completed"
                task.completed_at = _utcnow()
            continue
        handler = handlers.get(task.task_type)
        if handler is None:
            task.status = "unsupported"
            task.error_code = "unsupported_task_type"
            task.completed_at = _utcnow()
            session.flush()
            continue

        while True:
            attempt_started_at = _utcnow()
            claim = session.execute(
                update(WorkflowTask)
                .where(
                    WorkflowTask.id == task.id,
                    WorkflowTask.status == "pending",
                )
                .values(
                    status="running",
                    started_at=attempt_started_at,
                    completed_at=None,
                    error_code=None,
                )
            )
            session.commit()
            if claim.rowcount != 1:
                session.refresh(task)
                break
            task.status = "running"
            task.started_at = attempt_started_at
            task.completed_at = None
            task.error_code = None
            try:
                result = handler(task)
                if not isinstance(result, dict) or _json_size(result) > MAX_TASK_RESULT_BYTES:
                    raise ValueError("Task result must be a bounded JSON object.")
                references = result.get("references", [])
                if (
                    not isinstance(references, list)
                    or len(references) > 32
                    or _json_size(references) > 8192
                ):
                    raise ValueError("Task result references exceed their storage limit.")
                task.result_summary = result
                task.output_references = references
                task.status = "completed"
                task.completed_at = _utcnow()
                task.error_code = None
                workflow.current_step += 1
                session.flush()
                break
            except Exception as error:
                logger.warning(
                    "Workflow task failed (%s)",
                    type(error).__name__,
                )
                if (
                    task.idempotent
                    and not task.task_type.startswith("specialist_")
                    and task.retry_count < task.max_retries
                ):
                    task.retry_count += 1
                    task.status = "pending"
                    task.error_code = "retryable_task_failure"
                    session.commit()
                    continue
                task.status = "failed"
                code = getattr(error, "code", None)
                summary = getattr(error, "summary", None)
                task.error_code = (
                    code
                    if isinstance(code, str)
                    and code.isascii()
                    and code.replace("_", "").isalnum()
                    and 1 <= len(code) <= 64
                    else "task_execution_failed"
                )
                if isinstance(summary, str) and summary:
                    task.result_summary = {
                        "status": "failed",
                        "summary": summary[:1000],
                        "error_code": task.error_code,
                    }
                task.completed_at = _utcnow()
                session.flush()
                break
        session.commit()

    final_statuses = {task.status for task in tasks}
    if final_statuses and final_statuses == {"completed"}:
        workflow.status = "completed"
        workflow.error_code = None
    elif final_statuses.intersection({"pending", "running"}):
        workflow.status = "running"
        workflow.error_code = None
        workflow.completed_at = None
    elif "completed" in final_statuses:
        workflow.status = "partial"
        workflow.error_code = "workflow_partial"
    elif "cancelled" in final_statuses and final_statuses.issubset({"cancelled"}):
        workflow.status = "cancelled"
        workflow.error_code = "workflow_cancelled"
    elif "blocked" in final_statuses and not final_statuses.intersection(
        {"failed", "unsupported"}
    ):
        workflow.status = "blocked"
        workflow.error_code = "workflow_blocked"
    elif final_statuses.intersection({"failed", "unsupported"}):
        workflow.status = "failed"
        workflow.error_code = "workflow_task_failed"
    else:
        workflow.status = "blocked"
        workflow.error_code = "workflow_blocked"
    if workflow.status != "running":
        workflow.completed_at = _utcnow()
    session.commit()
    return workflow


def _topological_task_ids(
    tasks: list[WorkflowTask],
    dependencies: dict[str, list[str]],
) -> list[str]:
    order: list[str] = []
    pending = {task.id for task in tasks}
    while pending:
        ready = [
            task.id
            for task in tasks
            if task.id in pending
            and set(dependencies[task.id]).issubset(order)
        ]
        if not ready:
            raise WorkflowPlanError("Persisted task dependencies contain a cycle.")
        order.extend(ready)
        pending.difference_update(ready)
    return order
