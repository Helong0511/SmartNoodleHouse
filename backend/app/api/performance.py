"""管理端性能只读指标（Task 12）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import require_permission
from app.core.perf_metrics import WINDOW_HOURS, query_perf_metrics
from app.core.response import success

router = APIRouter(prefix="/admin/performance", tags=["性能监控"])


@router.get("/reco-metrics", dependencies=[require_permission("order:read")])
async def reco_metrics(
    request: Request,
    window_hours: int = WINDOW_HOURS,
    route: str | None = None,
):
    """近 24h 路由级 P50/P95/P99/QPS（Redis 滚动分钟桶聚合）。"""
    report = await query_perf_metrics(window_hours=window_hours, route=route)
    return success(report, request=request)
