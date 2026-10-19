"""add persisted schema architect analyses

Revision ID: c91d24fb0a53
Revises: 752e414cdd68
Create Date: 2026-10-19 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91d24fb0a53"
down_revision: Union[str, Sequence[str], None] = "752e414cdd68"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schema_analyses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
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
            "status IN ('Requested', 'Running', 'Completed', 'Failed')",
            name="ck_schema_analyses_status",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name="fk_schema_analyses_dataset_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schema_analyses"),
        sa.UniqueConstraint(
            "dataset_id",
            "analysis_key",
            name="uq_schema_analyses_dataset_key",
        ),
    )
    op.create_index(
        "ix_schema_analyses_dataset_id",
        "schema_analyses",
        ["dataset_id"],
        unique=False,
    )
    op.create_index(
        "ix_schema_analyses_analysis_key",
        "schema_analyses",
        ["analysis_key"],
        unique=False,
    )
    op.create_index(
        "ix_schema_analyses_dataset_created",
        "schema_analyses",
        ["dataset_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_schema_analyses_dataset_created", table_name="schema_analyses")
    op.drop_index("ix_schema_analyses_analysis_key", table_name="schema_analyses")
    op.drop_index("ix_schema_analyses_dataset_id", table_name="schema_analyses")
    op.drop_table("schema_analyses")
