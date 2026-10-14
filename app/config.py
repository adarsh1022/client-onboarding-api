"""Application settings, read from environment variables (or a .env file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the app runs with zero setup.
    # docker-compose.yml overrides this with a PostgreSQL URL.
    database_url: str = "sqlite:///./onboarding.db"

    # Uploads larger than this are rejected with HTTP 413.
    max_upload_bytes: int = 5 * 1024 * 1024


settings = Settings()
