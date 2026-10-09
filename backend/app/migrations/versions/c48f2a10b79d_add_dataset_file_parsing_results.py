"""add dataset file parsing results

Revision ID: c48f2a10b79d
Revises: 8eb5df4b59e5
Create Date: 2026-10-09 22:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c48f2a10b79d"
down_revision: Union[str, Sequence[str], None] = "8eb5df4b59e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_files",
        sa.Column("detected_format", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dataset_files",
        sa.Column("parsing_result", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_files", "parsing_result")
    op.drop_column("dataset_files", "detected_format")
