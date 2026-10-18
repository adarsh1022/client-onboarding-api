"""API-key authentication: hashing, the 401s, the CLI, the bootstrap key and DB URLs."""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cli, db
from app.auth import create_api_key, ensure_bootstrap_key, hash_key
from app.config import Settings, settings
from app.main import app
from app.models import ApiKey

CSV = b"Name,Email,Country\nAsha,asha@x.com,IN\n"
PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
}
BOOTSTRAP_KEY = "bootstrap-key-for-tests-0123456789abcdef"  # 40 characters


def all_keys(session_factory):
    with session_factory() as s:
        return list(s.scalars(select(ApiKey).order_by(ApiKey.id)))


# --- keys are stored hashed ---------------------------------------------------------------


def test_a_key_is_stored_only_as_a_hash(session_factory):
    with session_factory() as s:
        record, key = create_api_key(s, "demo")

    (stored,) = all_keys(session_factory)
    assert key.startswith("ob_") and len(key) > 40
    assert stored.key_hash == hash_key(key) and len(stored.key_hash) == 64
    assert stored.prefix == key[:7]
    # The plaintext isn't in any column.
    assert key not in {stored.name, stored.prefix, stored.key_hash}
    assert record.name == "demo"


def test_keys_are_unique_and_unpredictable(session_factory):
    with session_factory() as s:
        keys = {create_api_key(s, f"k{i}")[1] for i in range(5)}
    assert len(keys) == 5


# --- who gets in --------------------------------------------------------------------------


def test_a_valid_key_works(client):
    assert client.get("/customers").status_code == 200


def test_missing_wrong_empty_and_revoked_keys_get_the_same_401(
    client, anonymous_client, session_factory, api_key
):
    def deny(**headers):
        response = anonymous_client.get("/customers", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "ApiKey"
        return response.json()["detail"]

    messages = {
        deny(),
        deny(**{"X-API-Key": ""}),
        deny(**{"X-API-Key": "ob_not-a-real-key"}),
        deny(**{"X-API-Key": api_key[:-1]}),
        deny(**{"X-API-Key": hash_key(api_key)}),  # the stored hash is not the key
    }

    with session_factory() as s:
        s.scalar(select(ApiKey)).revoked_at = datetime.now(UTC)
        s.commit()
    messages.add(deny(**{"X-API-Key": api_key}))

    assert len(messages) == 1  # nothing tells an attacker which case it was
    assert "X-API-Key" in messages.pop()


def test_the_header_name_is_case_insensitive(anonymous_client, api_key):
    assert anonymous_client.get("/customers", headers={"x-api-key": api_key}).status_code == 200


def test_public_endpoints_need_no_key(anonymous_client):
    for path in ("/health", "/health/ready", "/docs", "/redoc", "/openapi.json"):
        assert anonymous_client.get(path).status_code == 200, path


def protected_routes():
    """Every (method, path) the app exposes, from its own OpenAPI schema, minus the public ones."""
    for path, operations in app.openapi()["paths"].items():
        if path not in PUBLIC_PATHS:
            for method in operations:
                yield method.upper(), re.sub(r"\{[^}]+\}", "1", path)


def test_every_other_endpoint_returns_401_without_a_key(anonymous_client):
    routes = list(protected_routes())
    assert len(routes) >= 9, "the route walk found too few routes to be trusted"

    for method, path in routes:
        response = anonymous_client.request(method, path)
        assert response.status_code == 401, f"{method} {path} answered {response.status_code}"


def test_the_key_is_checked_before_the_request_is_validated(anonymous_client):
    # No file at all: still a 401, not a 422 that would reveal the endpoint's shape.
    assert anonymous_client.post("/uploads").status_code == 401


def test_swagger_docs_offer_an_authorize_button(anonymous_client):
    schemes = anonymous_client.get("/openapi.json").json()["components"]["securitySchemes"]
    (scheme,) = schemes.values()
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "header", "X-API-Key")


def test_a_huge_declared_body_is_refused_before_it_is_read_even_without_a_key(
    anonymous_client, monkeypatch
):
    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    response = anonymous_client.post("/uploads", headers={"Content-Length": "999999999"})
    assert response.status_code == 413


# --- the CLI ------------------------------------------------------------------------------


def test_cli_creates_a_key_that_works_and_shows_it_once(
    client, anonymous_client, session_factory, capsys
):
    assert cli.main(["create-key", "--name", "  demo user  "]) == 0
    printed = capsys.readouterr().out
    key = next(line for line in printed.splitlines() if line.startswith("ob_"))

    assert "only once" in printed
    assert anonymous_client.get("/customers", headers={"X-API-Key": key}).status_code == 200
    created = all_keys(session_factory)[-1]
    assert created.name == "demo user" and created.key_hash == hash_key(key)

    assert cli.main(["list-keys"]) == 0
    listing = capsys.readouterr().out
    assert created.prefix in listing and "demo user" in listing and "active" in listing
    assert key not in listing  # keys are never shown again


def test_cli_revoke_stops_a_key_and_unknown_ids_are_reported(
    client, anonymous_client, session_factory, capsys
):
    cli.main(["create-key", "--name", "temp"])
    key = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("ob_"))
    key_id = all_keys(session_factory)[-1].id
    headers = {"X-API-Key": key}
    assert anonymous_client.get("/customers", headers=headers).status_code == 200

    assert cli.main(["revoke-key", str(key_id)]) == 0
    assert anonymous_client.get("/customers", headers=headers).status_code == 401
    assert cli.main(["revoke-key", str(key_id)]) == 0  # revoking twice is harmless
    assert cli.main(["revoke-key", "9999"]) == 1
    assert "No API key with id 9999" in capsys.readouterr().err

    cli.main(["list-keys"])
    assert "revoked" in capsys.readouterr().out


def test_cli_list_with_no_keys_says_how_to_make_one(session_factory, capsys):
    assert cli.main(["list-keys"]) == 0
    assert "create-key" in capsys.readouterr().out


# --- BOOTSTRAP_API_KEY: for hosts with no shell -------------------------------------------


def test_the_bootstrap_key_is_created_hashed_and_works(
    client, anonymous_client, session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "bootstrap_api_key", BOOTSTRAP_KEY)
    with TestClient(app):  # starting the app runs ensure_bootstrap_key
        pass

    boot = [k for k in all_keys(session_factory) if k.name == "bootstrap"]
    assert len(boot) == 1 and boot[0].key_hash == hash_key(BOOTSTRAP_KEY)
    assert BOOTSTRAP_KEY not in (boot[0].name, boot[0].prefix, boot[0].key_hash)
    headers = {"X-API-Key": BOOTSTRAP_KEY}
    assert anonymous_client.get("/customers", headers=headers).status_code == 200


def test_the_bootstrap_key_is_idempotent_and_rotates(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_api_key", BOOTSTRAP_KEY)
    ensure_bootstrap_key()
    ensure_bootstrap_key()
    assert len(all_keys(session_factory)) == 1

    new_key = "a-different-bootstrap-key-0123456789abcdef"
    monkeypatch.setattr(settings, "bootstrap_api_key", new_key)
    ensure_bootstrap_key()
    old, new = all_keys(session_factory)
    assert old.revoked_at is not None and new.revoked_at is None
    assert new.key_hash == hash_key(new_key)


def test_other_keys_survive_a_bootstrap_rotation(session_factory, monkeypatch):
    with session_factory() as s:
        create_api_key(s, "human")
    monkeypatch.setattr(settings, "bootstrap_api_key", BOOTSTRAP_KEY)
    ensure_bootstrap_key()
    human, _ = all_keys(session_factory)
    assert human.name == "human" and human.revoked_at is None


def test_a_revoked_bootstrap_key_stays_revoked_after_a_restart(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_api_key", BOOTSTRAP_KEY)
    ensure_bootstrap_key()
    cli.main(["revoke-key", str(all_keys(session_factory)[0].id)])
    ensure_bootstrap_key()

    (only,) = all_keys(session_factory)
    assert only.revoked_at is not None


def test_a_weak_bootstrap_key_stops_the_app_from_starting(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_api_key", "too-short")
    with pytest.raises(RuntimeError, match="at least 32"), TestClient(app):
        pass
    assert all_keys(session_factory) == []


def test_no_bootstrap_key_means_nothing_is_created(session_factory):
    ensure_bootstrap_key()
    assert all_keys(session_factory) == []


# --- DATABASE_URL as Neon and Render write it ---------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        (
            "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
            "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require",
        ),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./onboarding.db", "sqlite:///./onboarding.db"),
    ],
)
def test_database_urls_are_pointed_at_the_psycopg_driver(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected


def test_a_neon_style_url_builds_an_engine_for_psycopg():
    url = Settings(_env_file=None, database_url="postgresql://u:p@ep-x.neon.tech/db").database_url
    engine = db.make_engine(url)  # lazy: builds the engine without connecting
    assert engine.url.drivername == "postgresql+psycopg"
    engine.dispose()
