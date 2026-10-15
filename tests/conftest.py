import functools

import pytest
from anthropic.resources.messages.messages import Messages
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def offline_suggester(monkeypatch):
    """Tests use the free heuristic suggester unless they pass in a fake, and never call
    the real Anthropic API or a local Ollama.

    Even if a developer has ANTHROPIC_API_KEY or SUGGESTER in their environment or .env,
    the app sees no key and the heuristic suggester, and any call that still reaches the
    Anthropic SDK fails loudly.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "suggester", "heuristic")

    @functools.wraps(Messages.create)
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to call the real Anthropic API; use a fake suggester")

    monkeypatch.setattr(Messages, "create", refuse)


@pytest.fixture
def session_factory(monkeypatch):
    """A fresh in-memory SQLite database for every test."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Background tasks look up db.SessionLocal at request time, so patch it too.
    monkeypatch.setattr(db, "SessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[db.get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
