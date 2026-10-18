"""Customer endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import db
from app.schemas import CustomerOut, Page
from app.services import customers as customers_service

router = APIRouter(tags=["customers"])


@router.get("/customers", response_model=Page[CustomerOut])
def list_customers(
    country: str | None = Query(None, min_length=2, max_length=2, description="ISO code, e.g. IN"),
    q: str | None = Query(None, min_length=1, description="Search name or email"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db.get_session),
) -> dict:
    """Clean customer records, newest ids last."""
    items, total = customers_service.list_customers(session, country, q, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
