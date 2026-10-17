"""add preprocessing executions table

Revision ID: 752e414cdd68
Revises: b27c49e1d8f3
Create Date: 2026-10-18 00:01:24.047688
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "752e414cdd68"
down_revision: Union[str, Sequence[str], None] = "b27c49e1d8f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "preprocessing_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="Requested"),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
        sa.Column("artifact_rows", sa.Integer(), nullable=True),
        sa.Column("artifact_columns", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('Requested', 'Running', 'Completed', 'Failed')",
            name="ck_preprocessing_executions_status",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_preprocessing_executions_dataset_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["dataset_files.id"],
            name="fk_preprocessing_executions_file_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_preprocessing_executions"),
        sa.UniqueConstraint("file_id", "execution_key", name="uq_preprocessing_executions_file_key"),
    )
    op.create_index(
        "ix_preprocessing_executions_dataset_id",
        "preprocessing_executions",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_preprocessing_executions_file_id",
        "preprocessing_executions",
        ["file_id"],
        unique=False,
    )
    op.create_index(
        "ix_preprocessing_executions_execution_key",
        "preprocessing_executions",
        ["execution_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_preprocessing_executions_execution_key", table_name="preprocessing_executions")
    op.drop_index("ix_preprocessing_executions_file_id", table_name="preprocessing_executions")
    op.drop_index("ix_preprocessing_executions_dataset_id", table_name="preprocessing_executions")
    op.drop_table("preprocessing_executions")
