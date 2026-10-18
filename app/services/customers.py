"""Reading customers."""

from sqlalchemy.orm import Session

from app.models import Customer
from app.repositories import customers as customers_repo


def list_customers(
    session: Session, country: str | None, q: str | None, limit: int, offset: int
) -> tuple[list[Customer], int]:
    """One page of customers (filtered by country and/or search text) and the total."""
    return customers_repo.search(session, country, q, limit, offset)
