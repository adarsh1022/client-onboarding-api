"""Queries for the `uploads` and `row_errors` tables."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RowError, Upload


def get(session: Session, upload_id: int) -> Upload | None:
    """The upload with this id, or None."""
    return session.get(Upload, upload_id)


def add(session: Session, upload: Upload) -> None:
    """Queue a new upload for insertion (the caller commits)."""
    session.add(upload)


def has_stored_content(session: Session, upload_id: int) -> bool:
    """True if the upload's original file bytes are stored.

    `Upload.content` is a deferred column, so this asks the database directly instead of
    loading up to MAX_UPLOAD_BYTES just to check for None.
    """
    found = session.scalar(
        select(Upload.id).where(Upload.id == upload_id, Upload.content.is_not(None))
    )
    return found is not None


def list_errors(
    session: Session, upload_id: int, limit: int, offset: int
) -> tuple[list[RowError], int]:
    """One page of an upload's row errors in file order, plus the total count."""
    base = select(RowError).where(RowError.upload_id == upload_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    items = session.scalars(
        base.order_by(RowError.row_number, RowError.id).limit(limit).offset(offset)
    ).all()
    return list(items), total
