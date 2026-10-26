"""add durable workflows, tasks, and task dependencies

Revision ID: e4a2d7c91b60
Revises: da7304d9fe22
Create Date: 2026-10-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4a2d7c91b60"
down_revision: Union[str, Sequence[str], None] = "da7304d9fe22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("source_scope", sa.JSON(), nullable=True),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.Column("final_artifact_references", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', "
            "'blocked', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        sa.CheckConstraint(
            "current_step >= 0",
            name="ck_workflow_executions_current_step",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_workflow_executions_owner_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_workflow_executions_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_workflow_executions_dataset_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_executions"),
    )
    op.create_index(
        "ix_workflow_executions_owner_user_id",
        "workflow_executions",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_workflow_executions_conversation_id",
        "workflow_executions",
        ["conversation_id"],
    )
    op.create_index(
        "ix_workflow_executions_dataset_id",
        "workflow_executions",
        ["dataset_id"],
    )
    op.create_index(
        "ix_workflow_executions_owner_status_updated",
        "workflow_executions",
        ["owner_user_id", "status", "updated_at"],
    )

    op.create_table(
        "workflow_tasks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("task_key", sa.String(length=80), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("idempotent", sa.Boolean(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("file_id", sa.Integer(), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("input_references", sa.JSON(), nullable=False),
        sa.Column("output_references", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'partial', 'failed', "
            "'blocked', 'cancelled', 'skipped', 'unsupported')",
            name="ck_workflow_tasks_status",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND max_retries >= 0 AND retry_count <= max_retries",
            name="ck_workflow_tasks_retry_bounds",
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 300",
            name="ck_workflow_tasks_timeout_bounds",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_workflow_tasks_sequence"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflow_executions.id"],
            name="fk_workflow_tasks_workflow_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_tasks"),
        sa.UniqueConstraint(
            "workflow_id",
            "task_key",
            name="uq_workflow_tasks_workflow_key",
        ),
    )
    op.create_index("ix_workflow_tasks_workflow_id", "workflow_tasks", ["workflow_id"])
    op.create_index(
        "ix_workflow_tasks_workflow_status",
        "workflow_tasks",
        ["workflow_id", "status"],
    )

    op.create_table(
        "workflow_task_dependencies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("depends_on_task_id", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "task_id != depends_on_task_id",
            name="ck_workflow_task_no_self_dependency",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["workflow_tasks.id"],
            name="fk_workflow_task_dependencies_task_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["workflow_tasks.id"],
            name="fk_workflow_task_dependencies_depends_on_task_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_task_dependencies"),
        sa.UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_workflow_task_dependency",
        ),
    )
    op.create_index(
        "ix_workflow_task_dependencies_task_id",
        "workflow_task_dependencies",
        ["task_id"],
    )
    op.create_index(
        "ix_workflow_task_dependencies_depends_on_task_id",
        "workflow_task_dependencies",
        ["depends_on_task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_task_dependencies_depends_on_task_id",
        table_name="workflow_task_dependencies",
    )
    op.drop_index(
        "ix_workflow_task_dependencies_task_id",
        table_name="workflow_task_dependencies",
    )
    op.drop_table("workflow_task_dependencies")
    op.drop_index("ix_workflow_tasks_workflow_status", table_name="workflow_tasks")
    op.drop_index("ix_workflow_tasks_workflow_id", table_name="workflow_tasks")
    op.drop_table("workflow_tasks")
    op.drop_index(
        "ix_workflow_executions_owner_status_updated",
        table_name="workflow_executions",
    )
    op.drop_index("ix_workflow_executions_dataset_id", table_name="workflow_executions")
    op.drop_index(
        "ix_workflow_executions_conversation_id",
        table_name="workflow_executions",
    )
    op.drop_index(
        "ix_workflow_executions_owner_user_id",
        table_name="workflow_executions",
    )
    op.drop_table("workflow_executions")
