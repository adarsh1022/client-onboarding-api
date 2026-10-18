"""Suggestion review endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import db
from app.models import MappingSuggestion
from app.schemas import ApproveIn, SuggestionOut
from app.services import suggestions as suggestions_service

router = APIRouter(tags=["suggestions"])


@router.get("/uploads/{upload_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    upload_id: int, session: Session = Depends(db.get_session)
) -> list[MappingSuggestion]:
    """AI-suggested mappings for headers the service didn't recognise."""
    return suggestions_service.list_for_upload(session, upload_id)


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
    return suggestions_service.approve(session, suggestion_id, payload.field if payload else None)


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
def reject_suggestion(
    suggestion_id: int, session: Session = Depends(db.get_session)
) -> MappingSuggestion:
    """Dismiss a suggestion. It won't be applied, and the header isn't asked about again."""
    return suggestions_service.reject(session, suggestion_id)
