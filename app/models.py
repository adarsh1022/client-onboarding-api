"""Database tables.

Client                - a client company that sends us files.
ClientMappingProfile  - a header -> canonical field mapping a client's reviewer approved.
Upload                - one file a client sent us, and how processing went.
MappingSuggestion     - an AI-suggested mapping for one unknown header of an upload.
Customer              - a clean, validated customer record (the "target" table).
RowError              - one problem found in one row of an upload, so the client can fix it.
"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UploadStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    # Required columns could only be matched by a pending AI suggestion.
    NEEDS_REVIEW = "needs_review"


class SuggestionStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClientMappingProfile(Base):
    """Approved mappings for one client, reused on that client's later uploads."""

    __tablename__ = "client_mapping_profiles"
    __table_args__ = (UniqueConstraint("client_id", "normalized_header"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    normalized_header: Mapped[str] = mapped_column(String(255))
    canonical_field: Mapped[str] = mapped_column(String(50))


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", name="fk_uploads_client_id"), default=None, index=True
    )
    # The original file bytes, kept so the upload can be reprocessed. Deferred so
    # ordinary Upload queries don't pull up to MAX_UPLOAD_BYTES from the database.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, default=None, deferred=True)
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


class MappingSuggestion(Base):
    """An AI suggestion for one header the static aliases and client profile didn't know.

    A human approves or rejects it. `suggested_field` is what the model said (None when
    it couldn't tell); `approved_field` is what the reviewer chose, which may differ.
    """

    __tablename__ = "mapping_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"), index=True)
    original_header: Mapped[str] = mapped_column(String(255))
    suggested_field: Mapped[str | None] = mapped_column(String(50), default=None)
    approved_field: Mapped[str | None] = mapped_column(String(50), default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=SuggestionStatus.PENDING)


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
