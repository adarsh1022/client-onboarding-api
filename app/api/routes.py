"""HTTP endpoints."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.ingest.service import process_upload
from app.models import Customer, RowError, Upload
from app.schemas import CustomerOut, Page, RowErrorOut, UploadOut

router = APIRouter()

ALLOWED_EXTENSIONS = (".csv", ".xlsx")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_202_ACCEPTED)
async def create_upload(
    file: UploadFile,
    background: BackgroundTasks,
    session: Session = Depends(db.get_session),
) -> Upload:
    """Accept a client file and process it in the background.

    Returns 202 straight away with the upload record; poll
    GET /uploads/{id} until status is "completed" or "failed".
    """
    filename = file.filename or ""
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .csv and .xlsx files are supported"
        )

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    upload = Upload(filename=filename)
    session.add(upload)
    session.commit()

    background.add_task(process_upload, db.SessionLocal, upload.id, content)
    return upload


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: int, session: Session = Depends(db.get_session)) -> Upload:
    upload = session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return upload


@router.get("/uploads/{upload_id}/errors", response_model=Page[RowErrorOut])
def list_upload_errors(
    upload_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db.get_session),
) -> dict:
    if session.get(Upload, upload_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")

    base = select(RowError).where(RowError.upload_id == upload_id)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    items = session.scalars(
        base.order_by(RowError.row_number, RowError.id).limit(limit).offset(offset)
    ).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/customers", response_model=Page[CustomerOut])
def list_customers(
    country: str | None = Query(None, min_length=2, max_length=2, description="ISO code, e.g. IN"),
    q: str | None = Query(None, min_length=1, description="Search name or email"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db.get_session),
) -> dict:
    stmt = select(Customer)
    if country:
        stmt = stmt.where(Customer.country_code == country.upper())
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Customer.name).like(pattern), Customer.email.like(pattern))
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = session.scalars(stmt.order_by(Customer.id).limit(limit).offset(offset)).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}
