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


class DatasetFile(Base):
    __tablename__ = "dataset_files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Processing', 'Ready', 'Failed')",
            name="ck_dataset_files_status",
        ),
        UniqueConstraint(
            "dataset_id",
            "idempotency_key",
            name="uq_dataset_files_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    storage_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    file_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    checksum: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    detected_format: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    parsing_result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    profile_result: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="Processing",
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(64),
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
