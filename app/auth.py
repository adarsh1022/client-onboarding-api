"""API-key authentication.

Clients send their key in an `X-API-Key` header. Only a SHA-256 hash of each key is stored.
Keys are long random strings, so a fast hash is enough: unlike a password, a key can't be
guessed from a dictionary, and the hash can't be reversed to get the key back.

Create keys with the CLI (`python -m app.cli create-key --name demo`), or, on a host with
no shell, set BOOTSTRAP_API_KEY (see ensure_bootstrap_key).
"""

import hashlib
import logging
import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.models import ApiKey, utcnow

log = logging.getLogger(__name__)

KEY_PREFIX = "ob_"
PREFIX_CHARS = 7  # how much of a key is stored in the clear, only to tell keys apart
MIN_BOOTSTRAP_KEY_CHARS = 32
BOOTSTRAP_NAME = "bootstrap"

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,  # we answer with our own 401, and /docs shows an Authorize button
    description="An API key. Create one with `python -m app.cli create-key`.",
)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def create_api_key(session: Session, name: str) -> tuple[ApiKey, str]:
    """Create and store a key. The plaintext is returned once and never stored."""
    key = generate_key()
    record = ApiKey(name=name, prefix=key[:PREFIX_CHARS], key_hash=hash_key(key))
    session.add(record)
    session.commit()
    return record, key


def require_api_key(
    key: str | None = Security(api_key_header),
    session: Session = Depends(db.get_session),
) -> ApiKey:
    """FastAPI dependency: the ApiKey behind the X-API-Key header, or a 401."""
    if key:
        record = session.scalar(
            select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None))
        )
        if record is not None:
            return record
    # One message for missing, unknown and revoked keys, so it tells an attacker nothing.
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Missing or invalid API key: send a valid key in the X-API-Key header",
        headers={"WWW-Authenticate": "ApiKey"},
    )


def ensure_bootstrap_key() -> None:
    """Make sure the key in BOOTSTRAP_API_KEY exists (hashed), when that setting is set.

    This is for hosts with no shell, such as Render's free tier, where you can't run the CLI.
    It is idempotent. Changing the setting rotates the key: bootstrap keys made from earlier
    values are revoked. A key you revoked by hand is not brought back by a restart.
    """
    key = settings.bootstrap_api_key
    if not key:
        return
    if len(key) < MIN_BOOTSTRAP_KEY_CHARS:
        raise RuntimeError(
            f"BOOTSTRAP_API_KEY is too short: use at least {MIN_BOOTSTRAP_KEY_CHARS} characters"
        )

    digest = hash_key(key)
    with db.SessionLocal() as session:
        existing = session.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
        if existing is None:
            session.add(ApiKey(name=BOOTSTRAP_NAME, prefix=key[:PREFIX_CHARS], key_hash=digest))
            log.info("created the bootstrap API key")
        elif existing.revoked_at is not None:
            log.warning("the bootstrap API key was revoked and stays revoked")

        stale = session.scalars(
            select(ApiKey).where(
                ApiKey.name == BOOTSTRAP_NAME,
                ApiKey.key_hash != digest,
                ApiKey.revoked_at.is_(None),
            )
        )
        for old in stale:
            old.revoked_at = utcnow()
        session.commit()
