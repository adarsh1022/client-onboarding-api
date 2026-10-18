"""Queries for `mapping_suggestions` and `client_mapping_profiles`."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClientMappingProfile, MappingSuggestion


def get(session: Session, suggestion_id: int) -> MappingSuggestion | None:
    """The suggestion with this id, or None."""
    return session.get(MappingSuggestion, suggestion_id)


def list_for_upload(session: Session, upload_id: int) -> list[MappingSuggestion]:
    """All suggestions made for one upload, oldest first."""
    return list(
        session.scalars(
            select(MappingSuggestion)
            .where(MappingSuggestion.upload_id == upload_id)
            .order_by(MappingSuggestion.id)
        )
    )


def get_profile_entry(
    session: Session, client_id: int, normalized_header: str
) -> ClientMappingProfile | None:
    """The client's saved mapping for this normalized header, or None."""
    return session.scalar(
        select(ClientMappingProfile).where(
            ClientMappingProfile.client_id == client_id,
            ClientMappingProfile.normalized_header == normalized_header,
        )
    )


def add_profile_entry(session: Session, entry: ClientMappingProfile) -> None:
    """Queue a new profile entry for insertion (the caller commits)."""
    session.add(entry)
