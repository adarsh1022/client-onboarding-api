"""Health endpoints. Public: load balancers and uptime checks call them without a key."""

import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import db
from app.redis_client import get_redis
from app.services import health as health_service

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up. Touches nothing, so it is cheap to call often.

    Keep the platform's health check (Render, a load balancer) pointed here. A check that
    queried the database would also keep a sleeping serverless database awake.
    """
    return {"status": "ok"}


@router.get("/health/ready")
def ready(
    session: Session = Depends(db.get_session),
    redis_client: redis.Redis | None = Depends(get_redis),
) -> JSONResponse:
    """Readiness: checks the database (required) and Redis (optional).

    200 with status "ok" or "degraded" (Redis configured but down: the app still works),
    or 503 with status "unavailable" when the database can't be reached.
    """
    result = health_service.check_readiness(session, redis_client)
    return JSONResponse(
        {"status": result.status, "checks": result.checks}, status_code=result.http_status
    )
