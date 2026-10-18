"""Queries for the `customers` table."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Customer


def search(
    session: Session, country: str | None, q: str | None, limit: int, offset: int
) -> tuple[list[Customer], int]:
    """One page of customers, optionally filtered, plus the total number that match.

    `country` is an ISO code. `q` matches part of the name or email, ignoring case.
    Values reach the database as bound parameters, never pasted into the SQL text.
    """
    stmt = select(Customer)
    if country:
        stmt = stmt.where(Customer.country_code == country.upper())
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Customer.name).like(pattern), Customer.email.like(pattern))
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = session.scalars(stmt.order_by(Customer.id).limit(limit).offset(offset)).all()
    return list(items), total
