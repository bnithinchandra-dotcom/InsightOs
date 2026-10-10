"""add dataset file quality analysis state and report

Revision ID: a18c37e9b4d2
Revises: f27ab95d8c31
Create Date: 2026-10-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a18c37e9b4d2"
down_revision: Union[str, Sequence[str], None] = "f27ab95d8c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_report", sa.JSON(), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("quality_analysis_completed_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        "UPDATE dataset_files SET quality_analysis_status = 'NotStarted' "
        "WHERE quality_analysis_status IS NULL"
    )
    op.alter_column("dataset_files", "quality_analysis_status", nullable=False)
    op.create_check_constraint(
        "ck_dataset_files_quality_analysis_status",
        "dataset_files",
        "quality_analysis_status IN "
        "('NotStarted', 'Requested', 'Running', 'Completed', "
        "'PartiallyCompleted', 'Failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_dataset_files_quality_analysis_status",
        "dataset_files",
        type_="check",
    )
    op.drop_column("dataset_files", "quality_analysis_completed_at")
    op.drop_column("dataset_files", "quality_analysis_started_at")
    op.drop_column("dataset_files", "quality_analysis_error_message")
    op.drop_column("dataset_files", "quality_analysis_error_code")
    op.drop_column("dataset_files", "quality_report")
    op.drop_column("dataset_files", "quality_analysis_key")
    op.drop_column("dataset_files", "quality_analysis_status")
