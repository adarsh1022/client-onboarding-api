"""Upload endpoints: send a file, check on it, see its row errors, run it again."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.ingest.factory import get_suggester
from app.ingest.service import process_upload
from app.ingest.suggester import MappingSuggester
from app.models import Upload
from app.ratelimit import limit_uploads
from app.schemas import Page, RowErrorOut, UploadOut
from app.services import uploads as uploads_service

router = APIRouter(tags=["uploads"])


@router.post(
    "/uploads",
    response_model=UploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit_uploads)],
)
async def create_upload(
    file: UploadFile,
    background: BackgroundTasks,
    client_id: int | None = Form(None, description="Optional: the client this file is from"),
    session: Session = Depends(db.get_session),
    suggester: MappingSuggester | None = Depends(get_suggester),
) -> Upload:
    """Accept a client file and process it in the background.

    Returns 202 straight away with the upload record; poll GET /uploads/{id} until
    status is "completed", "failed" or "needs_review". Needs review means required
    columns could only be matched by AI suggestions: see GET /uploads/{id}/suggestions.
    """
    filename = file.filename or ""
    uploads_service.check_filename(filename)
    content = await file.read(settings.max_upload_bytes + 1)
    upload = uploads_service.create_upload(session, filename, content, client_id)

    background.add_task(process_upload, db.SessionLocal, upload.id, suggester)
    return upload


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: int, session: Session = Depends(db.get_session)) -> Upload:
    """Status and counters of one upload."""
    return uploads_service.get_upload(session, upload_id)


@router.post(
    "/uploads/{upload_id}/reprocess",
    response_model=UploadOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit_uploads)],
)
def reprocess_upload(
    upload_id: int,
    background: BackgroundTasks,
    session: Session = Depends(db.get_session),
    suggester: MappingSuggester | None = Depends(get_suggester),
) -> Upload:
    """Run a stored upload through the pipeline again, e.g. after approving suggestions.

    Results of the earlier run are replaced. Customers are upserted by email, so the
    rows an earlier run loaded show up as updated rather than duplicated.
    """
    upload = uploads_service.mark_for_reprocess(session, upload_id)
    background.add_task(process_upload, db.SessionLocal, upload.id, suggester)
    return upload


@router.get("/uploads/{upload_id}/errors", response_model=Page[RowErrorOut])
def list_upload_errors(
    upload_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db.get_session),
) -> dict:
    """Row-level problems found in one upload, in file order."""
    items, total = uploads_service.list_errors(session, upload_id, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
