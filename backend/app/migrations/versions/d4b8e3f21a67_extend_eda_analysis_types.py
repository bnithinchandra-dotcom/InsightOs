"""allow persisted anomaly-detection EDA results

Revision ID: d4b8e3f21a67
Revises: 7a94c21b6e3f
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4b8e3f21a67"
down_revision: Union[str, Sequence[str], None] = "7a94c21b6e3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("eda_analyses") as batch_op:
        batch_op.drop_constraint(
            "ck_eda_analyses_analysis_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_eda_analyses_analysis_type",
            "analysis_type IN ('univariate', 'bivariate', 'multivariate', 'anomaly_detection')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    anomaly_rows = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM eda_analyses "
            "WHERE analysis_type = 'anomaly_detection'"
        )
    ).scalar_one()
    if anomaly_rows:
        raise RuntimeError(
            "Cannot remove anomaly_detection support while persisted anomaly analyses exist."
        )
    with op.batch_alter_table("eda_analyses") as batch_op:
        batch_op.drop_constraint(
            "ck_eda_analyses_analysis_type",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_eda_analyses_analysis_type",
            "analysis_type IN ('univariate', 'bivariate', 'multivariate')",
        )
