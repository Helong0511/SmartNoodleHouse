"""Separate process liveness from dependency readiness probes."""
from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.redis_client import get_redis

router = APIRouter(tags=["健康检查"])
# Root routes are used by the reverse proxy / infrastructure probes.
root_router = APIRouter(tags=["健康检查"])


async def liveness():
    """Process-only liveness: do not restart a healthy process for a dependency outage."""
    return {"status": "ok"}


async def readiness(response: Response, db: DbSession):
    """Readiness requires both MySQL and Redis; failures return HTTP 503."""
    checks: dict[str, bool] = {"database": False, "redis": False}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        pass
    try:
        r = await get_redis()
        await r.ping()
        checks["redis"] = True
    except Exception:
        pass

    ok = checks["database"] and checks["redis"]
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "checks": checks}


router.add_api_route("/health", liveness, methods=["GET"])
router.add_api_route("/ready", readiness, methods=["GET"])
root_router.add_api_route("/health", liveness, methods=["GET"])
root_router.add_api_route("/ready", readiness, methods=["GET"])
