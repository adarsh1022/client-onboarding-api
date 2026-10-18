"""Settings loaded from the environment, and the optional Redis client built from them."""

import pytest
from pydantic import ValidationError

from app import redis_client
from app.config import Settings, settings


@pytest.fixture(autouse=True)
def fresh_redis_client():
    redis_client.reset_redis_client()
    yield
    redis_client.reset_redis_client()


def make_settings(monkeypatch, **env) -> Settings:
    """Settings as if the process had been started with these environment variables."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_defaults_need_no_environment(monkeypatch):
    for name in ("LOG_LEVEL", "LOG_FORMAT", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    config = Settings(_env_file=None)
    assert config.log_level == "INFO"
    assert config.log_format == "json"
    assert config.redis_url is None
    assert config.redis_timeout_seconds == 1.0


def test_logging_settings_come_from_environment_variables(monkeypatch):
    config = make_settings(monkeypatch, LOG_LEVEL="debug", LOG_FORMAT=" TEXT ")
    assert (config.log_level, config.log_format) == ("DEBUG", "text")


def test_a_bad_log_level_is_refused_at_startup(monkeypatch):
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        make_settings(monkeypatch, LOG_LEVEL="loud")


def test_a_bad_log_format_is_refused_at_startup(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, LOG_FORMAT="xml")


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_redis_url_means_not_configured(monkeypatch, blank):
    assert make_settings(monkeypatch, REDIS_URL=blank).redis_url is None


def test_redis_url_is_read_from_the_environment(monkeypatch):
    config = make_settings(monkeypatch, REDIS_URL="rediss://default:pw@example.upstash.io:6379")
    assert config.redis_url == "rediss://default:pw@example.upstash.io:6379"


def test_no_redis_client_without_a_url(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)
    assert redis_client.get_redis() is None


def test_the_redis_client_is_built_lazily_and_reused(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6399/0")
    first = redis_client.get_redis()
    assert first is not None  # building it opens no connection
    assert redis_client.get_redis() is first


def test_a_changed_url_gets_a_new_client(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6399/0")
    first = redis_client.get_redis()
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6399/1")
    assert redis_client.get_redis() is not first


def test_the_client_uses_short_timeouts(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6399/0")
    monkeypatch.setattr(settings, "redis_timeout_seconds", 0.25)
    kwargs = redis_client.get_redis().connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == 0.25 and kwargs["socket_timeout"] == 0.25


def test_an_unreachable_redis_fails_fast_with_a_connection_error(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")  # nothing listens here
    with pytest.raises(Exception, match="onnect|refused|denied|forcibly"):
        redis_client.get_redis().ping()
