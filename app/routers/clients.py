"""Client endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import db
from app.models import Client
from app.schemas import ClientCreate, ClientOut
from app.services import clients as clients_service

router = APIRouter(tags=["clients"])


@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, session: Session = Depends(db.get_session)) -> Client:
    """Register a client. Pass its id as `client_id` when uploading, so approved column
    mappings are remembered for that client's later files."""
    return clients_service.create_client(session, payload.name)
