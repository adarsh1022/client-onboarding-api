"""Database tables.

Upload    - one file a client sent us, and how processing went.
Customer  - a clean, validated customer record (the "target" table).
RowError  - one problem found in one row of an upload, so the client can fix it.
"""

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UploadStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=UploadStatus.PENDING)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    errors: Mapped[list["RowError"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan", order_by="RowError.id"
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    signup_date: Mapped[date | None] = mapped_column(Date, default=None)

    source_upload_id: Mapped[int | None] = mapped_column(ForeignKey("uploads.id"), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RowError(Base):
    __tablename__ = "row_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)

    upload: Mapped[Upload] = relationship(back_populates="errors")
