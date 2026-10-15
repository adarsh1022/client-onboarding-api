"""HTTP endpoints."""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.ingest.factory import get_suggester
from app.ingest.mapping import HEADER_ALIASES, normalize_header
from app.ingest.service import process_upload
from app.ingest.suggester import MappingSuggester
from app.models import (
    Client,
    ClientMappingProfile,
    Customer,
    MappingSuggestion,
    RowError,
    SuggestionStatus,
    Upload,
    UploadStatus,
)
from app.schemas import (
    ApproveIn,
    ClientCreate,
    ClientOut,
    CustomerOut,
    Page,
    RowErrorOut,
    SuggestionOut,
    UploadOut,
)

router = APIRouter()

ALLOWED_EXTENSIONS = (".csv", ".xlsx")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, session: Session = Depends(db.get_session)) -> Client:
    """Register a client. Pass its id as `client_id` when uploading, so approved column
    mappings are remembered for that client's later files."""
    client = Client(name=payload.name)
    session.add(client)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A client with that name already exists"
        ) from None
    return client


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_202_ACCEPTED)
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
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .csv and .xlsx files are supported"
        )

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is too large")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    if client_id is not None and session.get(Client, client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")

    upload = Upload(filename=filename, client_id=client_id, content=content)
    session.add(upload)
    session.commit()

    background.add_task(process_upload, db.SessionLocal, upload.id, suggester)
    return upload


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: int, session: Session = Depends(db.get_session)) -> Upload:
    return _get_upload_or_404(session, upload_id)


@router.post(
    "/uploads/{upload_id}/reprocess", response_model=UploadOut, status_code=status.HTTP_202_ACCEPTED
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
    upload = _get_upload_or_404(session, upload_id)
    if upload.status in (UploadStatus.PENDING, UploadStatus.PROCESSING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload is still being processed")
    has_content = session.scalar(
        select(Upload.id).where(Upload.id == upload_id, Upload.content.is_not(None))
    )
    if has_content is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The original file was not stored")

    upload.status = UploadStatus.PENDING
    session.commit()
    background.add_task(process_upload, db.SessionLocal, upload.id, suggester)
    return upload


@router.get("/uploads/{upload_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    upload_id: int, session: Session = Depends(db.get_session)
) -> list[MappingSuggestion]:
    """AI-suggested mappings for headers the service didn't recognise."""
    _get_upload_or_404(session, upload_id)
    return list(
        session.scalars(
            select(MappingSuggestion)
            .where(MappingSuggestion.upload_id == upload_id)
            .order_by(MappingSuggestion.id)
        )
    )


@router.post("/suggestions/{suggestion_id}/approve", response_model=SuggestionOut)
def approve_suggestion(
    suggestion_id: int,
    payload: ApproveIn | None = None,
    session: Session = Depends(db.get_session),
) -> MappingSuggestion:
    """Accept a suggestion, or pass `{"field": "..."}` to choose a different field.

    The mapping applies when the upload is reprocessed. If the upload has a client, it is
    also saved to that client's profile and used for the client's later uploads.
    """
    suggestion = _get_pending_suggestion(session, suggestion_id)
    field = (payload.field if payload else None) or suggestion.suggested_field
    if field is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            'The model had no suggestion for this header: pass {"field": "..."} to choose one',
        )
    if field not in HEADER_ALIASES:
        raise HTTPException(
            422, f"Unknown field '{field}'. Choose one of: {', '.join(HEADER_ALIASES)}"
        )
    normalized = normalize_header(suggestion.original_header)
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This header can't be mapped")

    suggestion.status = SuggestionStatus.APPROVED
    suggestion.approved_field = field

    upload = session.get(Upload, suggestion.upload_id)
    if upload is not None and upload.client_id is not None:
        profile = session.scalar(
            select(ClientMappingProfile).where(
                ClientMappingProfile.client_id == upload.client_id,
                ClientMappingProfile.normalized_header == normalized,
            )
        )
        if profile is None:
            session.add(
                ClientMappingProfile(
                    client_id=upload.client_id, normalized_header=normalized, canonical_field=field
                )
            )
        else:
            profile.canonical_field = field
    session.commit()
    return suggestion


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
def reject_suggestion(
    suggestion_id: int, session: Session = Depends(db.get_session)
) -> MappingSuggestion:
    """Dismiss a suggestion. It won't be applied, and the header isn't asked about again."""
    suggestion = _get_pending_suggestion(session, suggestion_id)
    suggestion.status = SuggestionStatus.REJECTED
    session.commit()
    return suggestion


def _get_upload_or_404(session: Session, upload_id: int) -> Upload:
    upload = session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return upload


def _get_pending_suggestion(session: Session, suggestion_id: int) -> MappingSuggestion:
    suggestion = session.get(MappingSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found")
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Suggestion is already {suggestion.status}")
    return suggestion


@router.get("/uploads/{upload_id}/errors", response_model=Page[RowErrorOut])
def list_upload_errors(
    upload_id: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db.get_session),
) -> dict:
    _get_upload_or_404(session, upload_id)

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
