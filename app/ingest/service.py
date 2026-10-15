"""Glue between the API and the pipeline: runs one upload end to end.

Runs as a FastAPI background task, so it opens its own database session
(the request's session is already closed by the time this runs). The file
bytes come from the Upload row, which is also what makes a reprocess possible.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from app.ingest.pipeline import ingest
from app.ingest.reader import read_file
from app.ingest.suggester import MappingSuggester
from app.models import Upload, UploadStatus, utcnow

log = logging.getLogger(__name__)


def process_upload(
    session_factory: sessionmaker[Session],
    upload_id: int,
    suggester: MappingSuggester | None = None,
) -> None:
    with session_factory() as session:
        upload = session.get(Upload, upload_id)
        if upload is None:
            log.error("upload %s vanished before processing", upload_id)
            return

        upload.status = UploadStatus.PROCESSING
        session.commit()

        try:
            if upload.content is None:
                upload.status = UploadStatus.FAILED
                upload.error_message = "the original file was not stored; upload it again"
                upload.finished_at = utcnow()
                session.commit()
                return
            parsed = read_file(upload.filename, upload.content)
            ingest(session, upload, parsed, suggester=suggester)
        except Exception as exc:  # never leave an upload stuck in "processing"
            log.exception("upload %s failed", upload_id)
            session.rollback()
            upload = session.get(Upload, upload_id)
            upload.status = UploadStatus.FAILED
            upload.error_message = f"internal error: {exc.__class__.__name__}"
            upload.finished_at = utcnow()
            session.commit()
