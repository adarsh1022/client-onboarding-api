"""Structured JSON logging and the request ID that follows a request through the logs."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.logging_config import (
    NO_REQUEST_ID,
    JsonFormatter,
    get_request_id,
    request_id_var,
    setup_logging,
)
from app.middleware import REQUEST_ID_HEADER, RequestContextMiddleware, choose_request_id


@pytest.fixture(autouse=True)
def restore_logging():
    """setup_logging changes global state; put the app's normal setup back afterwards."""
    yield
    setup_logging(settings)


def request_records(caplog):
    return [r for r in caplog.records if r.name == "app.request"]


# --- the JSON formatter -------------------------------------------------------------------


def format_record(**kwargs) -> dict:
    record = logging.makeLogRecord({"name": "app.demo", "levelno": logging.INFO, **kwargs})
    record.levelname = "INFO"
    return json.loads(JsonFormatter().format(record))


def test_json_line_has_the_standard_fields():
    entry = format_record(msg="hello %s", args=("world",), request_id="abc123")
    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "app.demo"
    assert entry["request_id"] == "abc123"
    assert entry["timestamp"].endswith("+00:00")


def test_extra_fields_become_json_keys():
    entry = format_record(msg="done", request_id="r", upload_id=7, rows=120)
    assert entry["upload_id"] == 7 and entry["rows"] == 120


def test_uvicorns_colour_codes_are_left_out():
    entry = format_record(msg="Started", request_id="r", color_message="[36mStarted[0m")
    assert "color_message" not in entry


def test_non_json_values_do_not_break_the_line():
    entry = format_record(msg="odd", request_id="r", thing=object())
    assert entry["thing"].startswith("<object object")


def test_exceptions_are_included_as_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        entry = format_record(msg="failed", request_id="r", exc_info=sys.exc_info())
    assert "ValueError: boom" in entry["exception"]


def test_a_record_without_a_request_id_gets_a_dash():
    assert format_record(msg="startup")["request_id"] == NO_REQUEST_ID


# --- setup_logging ------------------------------------------------------------------------


def test_every_record_carries_the_request_id_from_the_context(caplog):
    token = request_id_var.set("ctx-1")
    try:
        logging.getLogger("app.demo").warning("inside")
    finally:
        request_id_var.reset(token)
    logging.getLogger("app.demo").warning("outside")

    inside, outside = [r for r in caplog.records if r.name == "app.demo"]
    assert inside.request_id == "ctx-1"
    assert outside.request_id == NO_REQUEST_ID


def test_setup_is_idempotent_and_leaves_other_handlers_alone():
    root = logging.getLogger()
    other = logging.NullHandler()
    root.addHandler(other)
    try:
        setup_logging(settings)
        setup_logging(settings)
        ours = [h for h in root.handlers if getattr(h, "_onboarding_log_handler", False)]
        assert len(ours) == 1
        assert other in root.handlers
    finally:
        root.removeHandler(other)


def test_text_format_can_be_chosen():
    setup_logging(Settings(_env_file=None, log_format="text"))
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_onboarding_log_handler", False)]
    assert not isinstance(ours[0].formatter, JsonFormatter)

    setup_logging(Settings(_env_file=None, log_format="json"))
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_onboarding_log_handler", False)]
    assert isinstance(ours[0].formatter, JsonFormatter)


def test_log_level_comes_from_settings():
    setup_logging(Settings(_env_file=None, log_level="warning"))
    assert logging.getLogger().level == logging.WARNING


def test_uvicorn_logs_are_routed_through_our_handler():
    setup_logging(settings)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate is True


# --- request IDs --------------------------------------------------------------------------


def test_choose_request_id_keeps_safe_values_and_replaces_the_rest():
    assert choose_request_id("abc-123_X.9") == "abc-123_X.9"
    for bad in (None, "", "has space", "new\nline", "x" * 65, "semi;colon", '"quoted"'):
        replaced = choose_request_id(bad)
        assert replaced != bad and len(replaced) == 32


def test_every_response_has_a_request_id(anonymous_client):
    first = anonymous_client.get("/health").headers[REQUEST_ID_HEADER]
    second = anonymous_client.get("/health").headers[REQUEST_ID_HEADER]
    assert len(first) == 32 and first != second


def test_a_safe_request_id_from_the_caller_is_kept(anonymous_client):
    response = anonymous_client.get("/health", headers={REQUEST_ID_HEADER: "trace-42"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-42"


def test_an_unsafe_request_id_from_the_caller_is_replaced(anonymous_client):
    response = anonymous_client.get("/health", headers={REQUEST_ID_HEADER: "x" * 200})
    assert response.headers[REQUEST_ID_HEADER] != "x" * 200


def test_error_responses_carry_the_request_id_too(anonymous_client):
    assert REQUEST_ID_HEADER in anonymous_client.get("/customers").headers  # a 401
    too_big = anonymous_client.post("/uploads", headers={"Content-Length": "999999999"})
    assert too_big.status_code == 413 and REQUEST_ID_HEADER in too_big.headers


def test_each_request_is_logged_once_with_its_id(anonymous_client, caplog):
    caplog.set_level(logging.INFO)
    response = anonymous_client.get("/health", headers={REQUEST_ID_HEADER: "trace-7"})

    (record,) = request_records(caplog)
    assert record.request_id == "trace-7"
    assert response.headers[REQUEST_ID_HEADER] == "trace-7"
    assert (record.method, record.path, record.status_code) == ("GET", "/health", 200)
    assert record.duration_ms >= 0


def test_the_logged_status_is_the_real_one(anonymous_client, caplog):
    caplog.set_level(logging.INFO)
    anonymous_client.get("/customers")
    (record,) = request_records(caplog)
    assert record.status_code == 401


def test_secrets_never_reach_the_request_log(client, caplog):
    caplog.set_level(logging.INFO)
    client.get(
        "/customers",
        params={"q": "search-secret"},
        headers={"Authorization": "Bearer tok-secret"},
    )

    (record,) = request_records(caplog)
    assert record.path == "/customers"
    # Only the app's own records: the test HTTP client logs its own URLs, which is not ours.
    app_logs = json.dumps(
        [
            r.__dict__ | {"msg": r.getMessage(), "args": None}
            for r in caplog.records
            if not r.name.startswith("httpx")
        ],
        default=str,
    )
    assert "search-secret" not in app_logs  # query strings are not logged
    assert "tok-secret" not in app_logs  # neither are headers
    assert client.headers["X-API-Key"] not in app_logs


def test_the_request_id_reaches_background_tasks(client, caplog, monkeypatch):
    """Background tasks run after the response is sent; their logs must keep the ID."""
    caplog.set_level(logging.INFO)

    def fake_process_upload(session_factory, upload_id, suggester=None):
        logging.getLogger("test.background").info("processing upload %s", upload_id)

    monkeypatch.setattr("app.routers.uploads.process_upload", fake_process_upload)
    csv = b"Name,Email,Country\nAsha,asha@x.com,IN\n"
    client.post(
        "/uploads",
        files={"file": ("a.csv", csv, "text/csv")},
        headers={REQUEST_ID_HEADER: "bg-1"},
    )

    (record,) = [r for r in caplog.records if r.name == "test.background"]
    assert record.request_id == "bg-1"


def test_the_id_is_cleared_after_the_request(anonymous_client):
    anonymous_client.get("/health", headers={REQUEST_ID_HEADER: "trace-9"})
    assert get_request_id() == NO_REQUEST_ID


def test_a_crashing_request_is_logged_with_its_traceback(caplog):
    caplog.set_level(logging.INFO)
    mini = FastAPI()
    mini.add_middleware(RequestContextMiddleware)

    @mini.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    response = TestClient(mini, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 500
    (record,) = request_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.status_code == 500
    assert record.exc_info[0] is RuntimeError
