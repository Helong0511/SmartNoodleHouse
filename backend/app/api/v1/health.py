"""轻量就绪探测：DB + Redis（供 LB/探针，无鉴权）。"""
from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.redis_client import get_redis

router = APIRouter(tags=["健康检查"])


@router.get("/health")
@router.get("/ready")
async def readiness(response: Response, db: DbSession):
    """存活+就绪：MySQL SELECT 1、Redis PING。失败 HTTP 503。"""
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
