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

    @field_validator("suggester", mode="before")
    @classmethod
    def _lowercase_suggester(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


settings = Settings()
