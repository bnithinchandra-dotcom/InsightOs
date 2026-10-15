"""add eda analyses table

Revision ID: b27c49e1d8f3
Revises: a18c37e9b4d2
Create Date: 2026-10-15 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b27c49e1d8f3"
down_revision: Union[str, Sequence[str], None] = "a18c37e9b4d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eda_analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("table_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("analysis_type", sa.String(length=32), nullable=False),
        sa.Column("analysis_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="Requested"),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_sha256", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('Requested', 'Running', 'Completed', 'PartiallyCompleted', 'Failed')",
            name="ck_eda_analyses_status",
        ),
        sa.CheckConstraint(
            "analysis_type IN ('univariate', 'bivariate', 'multivariate')",
            name="ck_eda_analyses_analysis_type",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_eda_analyses_dataset_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["dataset_files.id"],
            name="fk_eda_analyses_file_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_eda_analyses"),
        sa.UniqueConstraint("file_id", "analysis_key", name="uq_eda_analyses_file_key"),
    )
    op.create_index(
        "ix_eda_analyses_dataset_id",
        "eda_analyses",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_eda_analyses_file_id",
        "eda_analyses",
        ["file_id"],
        unique=False,
    )
    op.create_index(
        "ix_eda_analyses_analysis_key",
        "eda_analyses",
        ["analysis_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_eda_analyses_analysis_key", table_name="eda_analyses")
    op.drop_index("ix_eda_analyses_file_id", table_name="eda_analyses")
    op.drop_index("ix_eda_analyses_dataset_id", table_name="eda_analyses")
    op.drop_table("eda_analyses")
