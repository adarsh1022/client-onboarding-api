"""App entry point.  Run locally with:  uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import ensure_bootstrap_key, require_api_key
from app.config import settings
from app.errors import register_error_handlers
from app.logging_config import setup_logging
from app.middleware import RequestContextMiddleware
from app.routers import protected_routers, public_routers

setup_logging()

# Room for multipart boundaries and form fields on top of the largest allowed file.
BODY_OVERHEAD_BYTES = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_bootstrap_key()  # does nothing unless BOOTSTRAP_API_KEY is set
    yield


app = FastAPI(
    title="Client Data Onboarding API",
    description=(
        "Upload messy client customer files (CSV/Excel). The service maps their "
        "columns, validates every row, loads clean records into SQL, and reports "
        "row-level errors the client can fix.\n\n"
        "Every endpoint except `/health` and `/health/ready` needs an API key: click "
        "**Authorize** and paste it, or send it in the `X-API-Key` header."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
register_error_handlers(app)


@app.middleware("http")
async def reject_oversized_bodies(request: Request, call_next):
    """Turn away a request that announces a huge body before the server reads any of it.

    Without this, someone with no API key could still make the server spool a large upload
    to disk before the key is even checked. (A client that doesn't send Content-Length gets
    past this; the per-file size check in POST /uploads still applies.)
    """
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > settings.max_upload_bytes + BODY_OVERHEAD_BYTES:
        return JSONResponse({"detail": "Request body is too large"}, status_code=413)
    return await call_next(request)


# Added last so it is the outermost middleware: even the 413 above gets a request ID.
app.add_middleware(RequestContextMiddleware)

for public_router in public_routers:
    app.include_router(public_router)
for protected_router in protected_routers:
    app.include_router(protected_router, dependencies=[Depends(require_api_key)])
