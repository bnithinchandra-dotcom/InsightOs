"""add owner-scoped dashboard definitions

Revision ID: 7a94c21b6e3f
Revises: e4a2d7c91b60
Create Date: 2026-10-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a94c21b6e3f"
down_revision: Union[str, Sequence[str], None] = "e4a2d7c91b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_dashboards_owner_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dashboards"),
    )
    op.create_index(
        "ix_dashboards_owner_user_id",
        "dashboards",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_dashboards_owner_updated",
        "dashboards",
        ["owner_user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dashboards_owner_updated", table_name="dashboards")
    op.drop_index("ix_dashboards_owner_user_id", table_name="dashboards")
    op.drop_table("dashboards")
