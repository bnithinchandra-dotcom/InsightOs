from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.dataset import Dataset
from app.models.dataset_file import DatasetFile


class EDAAnalysis(Base):
    __tablename__ = "eda_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Requested', 'Running', 'Completed', 'PartiallyCompleted', 'Failed')",
            name="ck_eda_analyses_status",
        ),
        CheckConstraint(
            "analysis_type IN ('univariate', 'bivariate', 'multivariate', 'anomaly_detection')",
            name="ck_eda_analyses_analysis_type",
        ),
        UniqueConstraint(
            "file_id",
            "analysis_key",
            name="uq_eda_analyses_file_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    file_id: Mapped[int] = mapped_column(
        ForeignKey("dataset_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    table_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    analysis_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
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
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    dataset: Mapped[Dataset] = relationship()
    dataset_file: Mapped[DatasetFile] = relationship()
