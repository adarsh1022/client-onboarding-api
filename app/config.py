"""Application settings, read from environment variables (or a .env file)."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the app runs with zero setup.
    # docker-compose.yml overrides this with a PostgreSQL URL.
    database_url: str = "sqlite:///./onboarding.db"

    # Uploads larger than this are rejected with HTTP 413.
    max_upload_bytes: int = 5 * 1024 * 1024

    # Each API key may start this many uploads (or reprocesses) per window; 0 turns it off.
    # The counters live in memory, so they are per process and reset on restart.
    rate_limit_uploads: int = 10
    rate_limit_window_seconds: int = 60

    # Optional: an API key to create at startup (stored hashed, like any other). It is for
    # hosts with no shell to run `python -m app.cli create-key`. Must be 32+ characters.
    bootstrap_api_key: str | None = None

    # Who suggests mappings for headers the aliases don't know:
    #   heuristic - free, offline rules (the default; no setup, no cost)
    #   ollama    - a local open model served by Ollama (free, needs Ollama running)
    #   claude    - Claude via the Anthropic API (paid; needs ANTHROPIC_API_KEY, and without
    #               a key suggestions are skipped)
    suggester: Literal["heuristic", "ollama", "claude"] = "heuristic"

    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-5-5"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    # A local Ollama server: free and offline, no API key.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout_seconds: float = 60.0  # small local models can be slow on a CPU
    ollama_max_retries: int = 2

    @field_validator("database_url")
    @classmethod
    def _use_the_psycopg_driver(cls, value: str) -> str:
        # Neon, Render and Heroku hand out postgres:// or postgresql:// URLs, which
        # SQLAlchemy would read as "use psycopg2". We ship psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @field_validator("suggester", mode="before")
    @classmethod
    def _lowercase_suggester(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


settings = Settings()
