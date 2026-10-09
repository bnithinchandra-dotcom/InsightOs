"""add ingestion state and idempotency

Revision ID: ac7f4206a1d3
Revises: c48f2a10b79d
Create Date: 2026-10-09 23:12:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ac7f4206a1d3"
down_revision: Union[str, Sequence[str], None] = "c48f2a10b79d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("active_upload_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.execute("UPDATE dataset_files SET status = 'Ready' WHERE status IS NULL")
    op.alter_column("dataset_files", "status", nullable=False)
    op.create_check_constraint(
        "ck_dataset_files_status",
        "dataset_files",
        "status IN ('Processing', 'Ready', 'Failed')",
    )
    op.create_unique_constraint(
        "uq_dataset_files_idempotency",
        "dataset_files",
        ["dataset_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_dataset_files_idempotency",
        "dataset_files",
        type_="unique",
    )
    op.drop_constraint(
        "ck_dataset_files_status",
        "dataset_files",
        type_="check",
    )
    op.drop_column("dataset_files", "error_message")
    op.drop_column("dataset_files", "error_code")
    op.drop_column("dataset_files", "idempotency_key")
    op.drop_column("dataset_files", "status")
    op.drop_column("datasets", "active_upload_key")
