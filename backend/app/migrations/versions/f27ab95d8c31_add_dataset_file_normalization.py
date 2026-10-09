"""add dataset file normalization state and result

Revision ID: f27ab95d8c31
Revises: d31f82a4be21
Create Date: 2026-10-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f27ab95d8c31"
down_revision: Union[str, Sequence[str], None] = "d31f82a4be21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_files",
        sa.Column("normalization_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("normalization_result", sa.JSON(), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("normalization_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("normalization_error_message", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE dataset_files SET normalization_status = 'NotStarted' "
        "WHERE normalization_status IS NULL"
    )
    op.alter_column("dataset_files", "normalization_status", nullable=False)
    op.create_check_constraint(
        "ck_dataset_files_normalization_status",
        "dataset_files",
        "normalization_status IN ('NotStarted', 'Processing', 'Ready', 'Failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_dataset_files_normalization_status",
        "dataset_files",
        type_="check",
    )
    op.drop_column("dataset_files", "normalization_error_message")
    op.drop_column("dataset_files", "normalization_error_code")
    op.drop_column("dataset_files", "normalization_result")
    op.drop_column("dataset_files", "normalization_status")
