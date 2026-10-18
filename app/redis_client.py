"""The shared Redis connection, when one is configured.

Redis is optional. With no REDIS_URL, `get_redis` returns None and callers fall back to
the database (cache) or to in-memory counters (rate limiting). Short timeouts mean a dead
Redis fails in about a second instead of hanging a request.
"""

import redis

from app.config import settings

_client: redis.Redis | None = None
_client_url: str | None = None


def get_redis() -> redis.Redis | None:
    """The Redis client for REDIS_URL, or None when Redis isn't configured.

    Also used as a FastAPI dependency, which is how tests swap in a fake.
    """
    global _client, _client_url
    url = settings.redis_url
    if not url:
        return None
    if _client is None or _client_url != url:
        _client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
        )
        _client_url = url
    return _client


def reset_redis_client() -> None:
    """Forget the cached client (used by tests when they change REDIS_URL)."""
    global _client, _client_url
    _client = None
    _client_url = None
