"""Request middleware: a request ID for every request, and one log line when it finishes."""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# A caller may supply its own ID (so one ID can follow a request across services), but only
# a short, boring one. Anything else could smuggle newlines or junk into our logs.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

log = logging.getLogger("app.request")


def choose_request_id(supplied: str | None) -> str:
    """Use the caller's ID if it looks safe, otherwise make a new one."""
    if supplied and _SAFE_REQUEST_ID.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Tag each HTTP request with an ID, echo it in the response, and log its outcome.

    It is a plain ASGI middleware rather than `@app.middleware("http")` on purpose: the
    ID must still be set while FastAPI runs background tasks after the response is sent,
    and the plain form guarantees that.

    The log line has the method, path, status and duration. It leaves out the query string
    (it can hold search terms or tokens) and every header (they hold credentials).
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        supplied = headers.get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        request_id = choose_request_id(supplied)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500  # what the client gets if the app raises before responding

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            log.exception("request crashed", extra=_fields(scope, status_code, started))
            raise
        else:
            log.info("request completed", extra=_fields(scope, status_code, started))
        finally:
            request_id_var.reset(token)


def _fields(scope: Scope, status_code: int, started: float) -> dict[str, object]:
    return {
        "method": scope["method"],
        "path": scope["path"],
        "status_code": status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
