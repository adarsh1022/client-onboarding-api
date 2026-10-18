"""Liveness (/health) and readiness (/health/ready)."""

import fakeredis
import pytest
import redis
from sqlalchemy.exc import OperationalError

from app import db
from app.main import app
from app.redis_client import get_redis
from app.services.health import check_readiness


class BrokenSession:
    """Stands in for a database session whose connection is down."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("could not connect to db.internal:5432"))


class DeadRedis:
    """Stands in for a Redis client that can't be reached."""

    def ping(self):
        raise redis.ConnectionError("cannot reach cache.internal:6379")


@pytest.fixture
def with_redis():
    """Install a Redis double for the duration of a test: with_redis(some_client)."""

    def install(fake):
        app.dependency_overrides[get_redis] = lambda: fake

    yield install
    app.dependency_overrides.pop(get_redis, None)


def break_database():
    def broken_session():
        yield BrokenSession()

    app.dependency_overrides[db.get_session] = broken_session


# --- liveness -----------------------------------------------------------------------------


def test_liveness_is_always_ok_and_needs_no_key(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_liveness_does_not_touch_the_database_or_redis(anonymous_client, with_redis):
    break_database()
    with_redis(DeadRedis())
    assert anonymous_client.get("/health").status_code == 200


# --- readiness ----------------------------------------------------------------------------


def test_ready_needs_no_key(anonymous_client):
    assert anonymous_client.get("/health/ready").status_code == 200


def test_ready_without_redis_configured_is_ok(client):
    body = client.get("/health/ready").json()
    assert body == {
        "status": "ok",
        "checks": {"database": {"status": "ok"}, "redis": {"status": "not_configured"}},
    }


def test_ready_with_working_redis_is_ok(client, with_redis):
    with_redis(fakeredis.FakeRedis(decode_responses=True))
    body = client.get("/health/ready").json()
    assert body["status"] == "ok"
    assert body["checks"]["redis"] == {"status": "ok"}


def test_ready_with_redis_down_is_degraded_but_still_200(client, with_redis):
    with_redis(DeadRedis())
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == {"status": "ok"}
    assert body["checks"]["redis"] == {"status": "unavailable", "error": "ConnectionError"}


def test_ready_with_database_down_is_503(client):
    break_database()
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == {"status": "unavailable", "error": "OperationalError"}


def test_ready_reports_both_failures_together(client, with_redis):
    break_database()
    with_redis(DeadRedis())
    response = client.get("/health/ready")
    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["status"] == checks["redis"]["status"] == "unavailable"


def test_ready_never_leaks_connection_details(client, with_redis):
    break_database()
    with_redis(DeadRedis())
    text = client.get("/health/ready").text
    assert "db.internal" not in text and "cache.internal" not in text


def test_failures_are_logged_with_the_full_error(client, with_redis, caplog):
    with_redis(DeadRedis())
    client.get("/health/ready")
    assert "cache.internal" in caplog.text  # the log keeps the detail the response hides


# --- the check itself ---------------------------------------------------------------------


def test_check_readiness_summaries(session):
    assert check_readiness(session, None).status == "ok"
    assert check_readiness(session, fakeredis.FakeRedis()).status == "ok"

    degraded = check_readiness(session, DeadRedis())
    assert (degraded.status, degraded.http_status) == ("degraded", 200)

    down = check_readiness(BrokenSession(), None)
    assert (down.status, down.http_status) == ("unavailable", 503)
