"""Structured logging: one JSON object per line, each tagged with the request ID.

The request ID lives in a context variable. `RequestContextMiddleware` sets it when a
request arrives, and a log-record factory copies it onto every record, so any log call
made while handling that request (in a route, a service, or a background task started by
it) carries the same ID. Searching the logs for one ID shows everything one request did.
"""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

from app.config import Settings, settings

# "-" outside a request (startup, shutdown, a CLI command).
NO_REQUEST_ID = "-"
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=NO_REQUEST_ID
)

# Attributes every LogRecord has, plus ones we never want in the output: `color_message` is
# uvicorn's copy of the message with terminal colour codes in it.
_STANDARD_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}

_HANDLER_MARK = "_onboarding_log_handler"


def get_request_id() -> str:
    """The ID of the request being handled, or "-" when there is none."""
    return request_id_var.get()


class JsonFormatter(logging.Formatter):
    """Format a record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", NO_REQUEST_ID),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


_TEXT_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


def _install_request_id_on_records() -> None:
    """Make every LogRecord carry `request_id`, whichever handler ends up formatting it."""
    previous_factory = logging.getLogRecordFactory()
    if getattr(previous_factory, "adds_request_id", False):
        return  # already installed (setup_logging can be called more than once)

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    factory.adds_request_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def setup_logging(config: Settings = settings) -> None:
    """Send all logging to stdout in the configured format. Safe to call repeatedly."""
    _install_request_id_on_records()

    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(
        JsonFormatter() if config.log_format == "json" else logging.Formatter(_TEXT_FORMAT)
    )

    root = logging.getLogger()
    # Replace only the handler we installed before, so other handlers (a test's caplog,
    # for instance) are left alone.
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(config.log_level)

    # uvicorn installs its own handlers with its own format. Route its logs through ours,
    # and mute its access log: RequestContextMiddleware writes a richer line per request.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
