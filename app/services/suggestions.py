"""Reviewing the column-mapping suggestions made for an upload."""

from sqlalchemy.orm import Session

from app.errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from app.ingest.mapping import HEADER_ALIASES, normalize_header
from app.models import ClientMappingProfile, MappingSuggestion, SuggestionStatus
from app.repositories import suggestions as suggestions_repo
from app.repositories import uploads as uploads_repo
from app.services.uploads import get_upload


def list_for_upload(session: Session, upload_id: int) -> list[MappingSuggestion]:
    """All suggestions for the upload, or NotFoundError if there is no such upload."""
    get_upload(session, upload_id)
    return suggestions_repo.list_for_upload(session, upload_id)


def approve(
    session: Session, suggestion_id: int, field_override: str | None = None
) -> MappingSuggestion:
    """Accept a suggestion, optionally choosing a different field than the suggested one.

    The mapping applies the next time the upload is processed. If the upload belongs to a
    client, the mapping is also saved to that client's profile for its later files.
    """
    suggestion = _get_pending(session, suggestion_id)
    field = field_override or suggestion.suggested_field
    if field is None:
        raise BadRequestError(
            'The model had no suggestion for this header: pass {"field": "..."} to choose one'
        )
    if field not in HEADER_ALIASES:
        raise UnprocessableError(
            f"Unknown field '{field}'. Choose one of: {', '.join(HEADER_ALIASES)}"
        )
    normalized = normalize_header(suggestion.original_header)
    if not normalized:
        raise BadRequestError("This header can't be mapped")

    suggestion.status = SuggestionStatus.APPROVED
    suggestion.approved_field = field

    upload = uploads_repo.get(session, suggestion.upload_id)
    if upload is not None and upload.client_id is not None:
        entry = suggestions_repo.get_profile_entry(session, upload.client_id, normalized)
        if entry is None:
            suggestions_repo.add_profile_entry(
                session,
                ClientMappingProfile(
                    client_id=upload.client_id, normalized_header=normalized, canonical_field=field
                ),
            )
        else:
            entry.canonical_field = field
    session.commit()
    return suggestion


def reject(session: Session, suggestion_id: int) -> MappingSuggestion:
    """Dismiss a suggestion: it won't be applied, and the header isn't asked about again."""
    suggestion = _get_pending(session, suggestion_id)
    suggestion.status = SuggestionStatus.REJECTED
    session.commit()
    return suggestion


def _get_pending(session: Session, suggestion_id: int) -> MappingSuggestion:
    suggestion = suggestions_repo.get(session, suggestion_id)
    if suggestion is None:
        raise NotFoundError("Suggestion not found")
    if suggestion.status != SuggestionStatus.PENDING:
        raise ConflictError(f"Suggestion is already {suggestion.status}")
    return suggestion
