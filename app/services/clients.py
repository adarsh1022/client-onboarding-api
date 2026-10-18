"""Client registration."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ConflictError
from app.models import Client
from app.repositories import clients as clients_repo


def create_client(session: Session, name: str) -> Client:
    """Register a client. Names are unique, so a second client with that name is a conflict."""
    client = Client(name=name)
    clients_repo.add(session, client)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("A client with that name already exists") from None
    return client
