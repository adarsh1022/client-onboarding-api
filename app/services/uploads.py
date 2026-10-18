"""Accepting uploads, reading their status, and queueing reprocesses."""

from sqlalchemy.orm import Session

from app.config import settings
from app.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.models import RowError, Upload, UploadStatus
from app.repositories import clients as clients_repo
from app.repositories import uploads as uploads_repo

ALLOWED_EXTENSIONS = (".csv", ".xlsx")


def check_filename(filename: str) -> None:
    """Reject a file type we can't read. Checked before the body is read, so it is cheap."""
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise UnsupportedMediaTypeError("Only .csv and .xlsx files are supported")


def create_upload(session: Session, filename: str, content: bytes, client_id: int | None) -> Upload:
    """Validate a received file and save it as a new upload, ready to be processed.

    `content` may be at most one byte over the size limit: the caller reads
    `max_upload_bytes + 1` bytes, which is enough to tell "too large" from "just fits".
    """
    if len(content) > settings.max_upload_bytes:
        raise PayloadTooLargeError("File is too large")
    if not content:
        raise BadRequestError("File is empty")
    if client_id is not None and clients_repo.get(session, client_id) is None:
        raise NotFoundError("Client not found")

    upload = Upload(filename=filename, client_id=client_id, content=content)
    uploads_repo.add(session, upload)
    session.commit()
    return upload


def get_upload(session: Session, upload_id: int) -> Upload:
    """The upload, or NotFoundError."""
    upload = uploads_repo.get(session, upload_id)
    if upload is None:
        raise NotFoundError("Upload not found")
    return upload


def mark_for_reprocess(session: Session, upload_id: int) -> Upload:
    """Reset a finished upload to pending so it can be run through the pipeline again.

    Refuses while a run is in progress (two runs at once would race), and when the original
    file was never stored (uploads from before files were kept).
    """
    upload = get_upload(session, upload_id)
    if upload.status in (UploadStatus.PENDING, UploadStatus.PROCESSING):
        raise ConflictError("Upload is still being processed")
    if not uploads_repo.has_stored_content(session, upload_id):
        raise ConflictError("The original file was not stored")

    upload.status = UploadStatus.PENDING
    session.commit()
    return upload


def list_errors(
    session: Session, upload_id: int, limit: int, offset: int
) -> tuple[list[RowError], int]:
    """One page of the upload's row errors and the total, or NotFoundError."""
    get_upload(session, upload_id)
    return uploads_repo.list_errors(session, upload_id, limit, offset)
