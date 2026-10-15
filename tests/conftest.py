import functools

import pytest
from anthropic.resources.messages.messages import Messages
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db
from app.auth import create_api_key
from app.config import settings
from app.main import app
from app.ratelimit import limiter


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


@pytest.fixture(autouse=True)
def default_limits(monkeypatch):
    """Known rate limits and no bootstrap key, whatever the developer's environment says,
    and a fresh rate-limit counter for every test."""
    monkeypatch.setattr(settings, "rate_limit_uploads", 10)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "bootstrap_api_key", None)
    limiter.reset()
    yield
    limiter.reset()


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
def api_key(session_factory):
    """A valid API key, in plaintext."""
    with session_factory() as s:
        _, key = create_api_key(s, "tests")
    return key


@pytest.fixture
def client(session_factory, api_key):
    """A test client that sends a valid X-API-Key header on every request."""

    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[db.get_session] = override
    with TestClient(app, headers={"X-API-Key": api_key}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(client):
    """A test client with no API key (it shares the client fixture's test database)."""
    return TestClient(app)
