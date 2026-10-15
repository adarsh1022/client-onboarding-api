"""A small in-memory rate limiter for the upload endpoints, one budget per API key.

It keeps the recent request times of each key in memory, so it is per process and resets on
restart. That is right for a single small instance (the free demo). Behind several workers
or instances each would count separately, and you would want a shared store such as Redis.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.auth import require_api_key
from app.config import settings
from app.models import ApiKey


class RateLimiter:
    """Sliding window: at most `limit` hits per key in any `window` seconds."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[int, deque[float]] = {}

    def hit(self, key_id: int, limit: int, window: float) -> float | None:
        """Record a request. Returns None if allowed, else the seconds until it would be."""
        if limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key_id, deque())
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.0, window - (now - hits[0]))
            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def limit_uploads(api_key: ApiKey = Depends(require_api_key)) -> None:
    """FastAPI dependency for the endpoints that start an upload run: 429 over the limit."""
    limit, window = settings.rate_limit_uploads, settings.rate_limit_window_seconds
    wait = limiter.hit(api_key.id, limit, window)
    if wait is not None:
        seconds = max(1, math.ceil(wait))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Upload limit reached: {limit} per {window} seconds for this API key. "
            f"Try again in {seconds} seconds.",
            headers={"Retry-After": str(seconds)},
        )
