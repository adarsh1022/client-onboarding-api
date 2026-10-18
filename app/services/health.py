"""Readiness: can this instance do its job right now?

Liveness (`/health`) only says the process is up and costs nothing. Readiness goes and
checks the things the app depends on.

- The database is required: without it nothing works, so the answer is "unavailable" (503).
- Redis is optional: the app falls back to the database and to in-memory counters, so a
  Redis that is configured but unreachable only makes the answer "degraded" (still 200).
  Redis that isn't configured at all is normal, and reported as "not_configured".
"""

import logging
from dataclasses import dataclass

import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


@dataclass
class Readiness:
    status: str  # "ok", "degraded" or "unavailable"
    checks: dict[str, dict[str, str]]

    @property
    def http_status(self) -> int:
        return 503 if self.status == "unavailable" else 200


def check_readiness(session: Session, redis_client: redis.Redis | None) -> Readiness:
    """Check the database and Redis, and summarise."""
    database = _check_database(session)
    cache = _check_redis(redis_client)

    if database["status"] != "ok":
        overall = "unavailable"
    elif cache["status"] == "unavailable":
        overall = "degraded"
    else:
        overall = "ok"
    return Readiness(status=overall, checks={"database": database, "redis": cache})


def _check_database(session: Session) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        # The log gets the full error; the response only gets the class name, so a public
        # endpoint never shows connection strings or hostnames.
        log.warning("readiness: database check failed", exc_info=True)
        return {"status": "unavailable", "error": exc.__class__.__name__}
    return {"status": "ok"}


def _check_redis(redis_client: redis.Redis | None) -> dict[str, str]:
    if redis_client is None:
        return {"status": "not_configured"}
    try:
        redis_client.ping()
    except Exception as exc:
        log.warning("readiness: redis check failed", exc_info=True)
        return {"status": "unavailable", "error": exc.__class__.__name__}
    return {"status": "ok"}
