"""Per-key rate limiting on the upload endpoints."""

import pytest

from app import ratelimit
from app.auth import create_api_key
from app.config import settings
from app.ratelimit import RateLimiter

CSV = b"Name,Email,Country\nAsha,asha@x.com,IN\n"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# --- the limiter itself -------------------------------------------------------------------


def test_allows_up_to_the_limit_then_says_how_long_to_wait():
    clock = Clock()
    limiter = RateLimiter(clock)

    assert [limiter.hit(1, limit=3, window=60) for _ in range(3)] == [None, None, None]
    clock.now += 20
    assert limiter.hit(1, limit=3, window=60) == pytest.approx(40)  # oldest hit frees up in 40s


def test_the_window_slides():
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.hit(1, 2, 60)  # t=0
    clock.now += 30
    limiter.hit(1, 2, 60)  # t=30
    assert limiter.hit(1, 2, 60) is not None  # full

    clock.now += 31  # t=61: the first hit has aged out, the second hasn't
    assert limiter.hit(1, 2, 60) is None
    assert limiter.hit(1, 2, 60) is not None


def test_keys_have_separate_budgets():
    limiter = RateLimiter(Clock())
    assert limiter.hit(1, 1, 60) is None
    assert limiter.hit(1, 1, 60) is not None
    assert limiter.hit(2, 1, 60) is None


def test_a_limit_of_zero_turns_it_off_and_reset_clears_everything():
    limiter = RateLimiter(Clock())
    assert all(limiter.hit(1, 0, 60) is None for _ in range(100))

    limiter.hit(1, 1, 60)
    assert limiter.hit(1, 1, 60) is not None
    limiter.reset()
    assert limiter.hit(1, 1, 60) is None


def test_a_refused_hit_does_not_extend_the_wait():
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.hit(1, 1, 60)
    clock.now += 10
    first = limiter.hit(1, 1, 60)
    clock.now += 10
    second = limiter.hit(1, 1, 60)
    assert first == pytest.approx(50) and second == pytest.approx(40)


# --- over HTTP ----------------------------------------------------------------------------


def upload(client, **kwargs):
    return client.post("/uploads", files={"file": ("a.csv", CSV, "text/csv")}, **kwargs)


def test_uploads_over_the_limit_get_429_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_uploads", 3)

    assert [upload(client).status_code for _ in range(3)] == [202, 202, 202]
    blocked = upload(client)

    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    assert "3 per 60 seconds" in blocked.json()["detail"]
    assert str(blocked.headers["retry-after"]) in blocked.json()["detail"]


def test_only_uploads_are_limited_not_reads(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_uploads", 1)
    upload_id = upload(client).json()["id"]

    assert upload(client).status_code == 429
    for path in ("/customers", f"/uploads/{upload_id}", f"/uploads/{upload_id}/errors", "/health"):
        assert client.get(path).status_code == 200, path


def test_reprocessing_shares_the_upload_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_uploads", 2)
    upload_id = upload(client).json()["id"]  # 1 of 2
    assert client.post(f"/uploads/{upload_id}/reprocess").status_code == 202  # 2 of 2

    assert client.post(f"/uploads/{upload_id}/reprocess").status_code == 429
    assert upload(client).status_code == 429


def test_each_api_key_has_its_own_budget(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_uploads", 1)
    assert upload(client).status_code == 202
    assert upload(client).status_code == 429

    with session_factory() as s:
        _, other_key = create_api_key(s, "someone else")
    assert upload(client, headers={"X-API-Key": other_key}).status_code == 202


def test_requests_without_a_valid_key_do_not_use_up_anyones_budget(
    client, anonymous_client, monkeypatch
):
    monkeypatch.setattr(settings, "rate_limit_uploads", 1)
    for _ in range(5):
        assert upload(anonymous_client).status_code == 401

    assert upload(client).status_code == 202  # the real key's budget is untouched


def test_the_budget_comes_back_after_the_window(client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ratelimit, "limiter", RateLimiter(clock))
    monkeypatch.setattr(settings, "rate_limit_uploads", 1)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)

    assert upload(client).status_code == 202
    assert upload(client).status_code == 429
    clock.now += 61
    assert upload(client).status_code == 202


def test_a_limit_of_zero_disables_it_over_http(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_uploads", 0)
    assert all(upload(client).status_code == 202 for _ in range(15))


def test_the_default_is_ten_per_minute(monkeypatch):
    from app.config import Settings

    for name in ("RATE_LIMIT_UPLOADS", "RATE_LIMIT_WINDOW_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)
    assert (config.rate_limit_uploads, config.rate_limit_window_seconds) == (10, 60)
