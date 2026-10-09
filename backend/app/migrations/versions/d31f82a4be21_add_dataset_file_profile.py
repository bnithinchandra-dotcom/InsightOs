"""add dataset file profile results

Revision ID: d31f82a4be21
Revises: ac7f4206a1d3
Create Date: 2026-10-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d31f82a4be21"
down_revision: Union[str, Sequence[str], None] = "ac7f4206a1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_files",
        sa.Column("profile_result", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_files", "profile_result")
