from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


WORKFLOW_STATUSES = (
    "queued",
    "running",
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
)
WORKFLOW_TASK_STATUSES = (
    "pending",
    "running",
    "completed",
    "partial",
    "failed",
    "blocked",
    "cancelled",
    "skipped",
    "unsupported",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', "
            "'blocked', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        CheckConstraint(
            "current_step >= 0",
            name="ck_workflow_executions_current_step",
        ),
        Index(
            "ix_workflow_executions_owner_status_updated",
            "owner_user_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    source_scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_artifact_references: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    tasks: Mapped[list["WorkflowTask"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkflowTask.sequence",
    )


class WorkflowTask(Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed', "
            "'blocked', 'cancelled', 'skipped', 'unsupported')",
            name="ck_workflow_tasks_status",
        ),
        CheckConstraint(
            "retry_count >= 0 AND max_retries >= 0 AND retry_count <= max_retries",
            name="ck_workflow_tasks_retry_bounds",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300",
            name="ck_workflow_tasks_timeout_bounds",
        ),
        CheckConstraint("sequence > 0", name="ck_workflow_tasks_sequence"),
        UniqueConstraint(
            "workflow_id",
            "task_key",
            name="uq_workflow_tasks_workflow_key",
        ),
        Index("ix_workflow_tasks_workflow_status", "workflow_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_key: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotent: Mapped[bool] = mapped_column(nullable=False, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )

    workflow: Mapped[WorkflowExecution] = relationship(back_populates="tasks")
    dependencies: Mapped[list["WorkflowTaskDependency"]] = relationship(
        foreign_keys="WorkflowTaskDependency.task_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WorkflowTaskDependency(Base):
    __tablename__ = "workflow_task_dependencies"
    __table_args__ = (
        CheckConstraint("task_id != depends_on_task_id", name="ck_workflow_task_no_self_dependency"),
        UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_workflow_task_dependency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
