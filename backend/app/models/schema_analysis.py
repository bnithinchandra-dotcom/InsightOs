from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.dataset import Dataset


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SchemaAnalysis(Base):
    __tablename__ = "schema_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Requested', 'Running', 'Completed', 'Failed')",
            name="ck_schema_analyses_status",
        ),
        UniqueConstraint(
            "dataset_id",
            "analysis_key",
            name="uq_schema_analyses_dataset_key",
        ),
        Index("ix_schema_analyses_dataset_created", "dataset_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    analysis_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="Requested",
    )

    configuration: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    configuration_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    provenance: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow_naive,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        nullable=False,
    )

    dataset: Mapped[Dataset] = relationship()
