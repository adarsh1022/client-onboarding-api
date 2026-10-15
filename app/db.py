"""Database engine and session handling."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    # SQLite needs this flag because FastAPI may use the connection from a
    # different thread than the one that created it.
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    elif url.startswith("postgresql+psycopg"):
        # No server-side prepared statements, so it also works through a connection pooler
        # such as Neon's pooled endpoint.
        connect_args = {"prepare_threshold": None}
    # pool_pre_ping replaces connections the database closed while idle (Neon suspends
    # its compute after 5 idle minutes) instead of failing the next request.
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
