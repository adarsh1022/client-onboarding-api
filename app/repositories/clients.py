"""Queries for the `clients` table."""

from sqlalchemy.orm import Session

from app.models import Client


def get(session: Session, client_id: int) -> Client | None:
    """The client with this id, or None."""
    return session.get(Client, client_id)


def add(session: Session, client: Client) -> None:
    """Queue a new client for insertion (the caller commits)."""
    session.add(client)
