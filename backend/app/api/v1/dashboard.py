"""数据概览：今日/昨日/本月统计 + 门店分项 + 商品销量 + 支付/外卖。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import success
from app.models import Store
from app.services.revenue_stats import (
    build_period_stats,
    daily_series,
    delivery_kpi,
    payment_channel_stats,
    product_sales_since,
    registered_member_count,
    resolve_period,
    store_period_breakdown,
    visitor_count_since,
)
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/dashboard", tags=["数据统计"])


@router.get("", dependencies=[require_permission("order:read")])
async def get_dashboard(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    period: str = Query(default="today", pattern="^(today|yesterday|month)$"),
    store_id: int | None = Query(default=None, ge=1),
):
    effective_store = require_bound_store(user, store_id)
    since, until, period_label = resolve_period(period)  # type: ignore[arg-type]
    period_until = until if period == "yesterday" else None

    summary = await build_period_stats(
        db,
        since=since,
        until=period_until,
        period_label=period_label,
        store_id=effective_store,
    )

    store_name: str | None = None
    stores: list[dict] = []
    if effective_store is None:
        stores = await store_period_breakdown(db, since=since, until=period_until)
    else:
        store_name = (
            await db.execute(select(Store.name).where(Store.id == effective_store))
        ).scalar_one_or_none()

    multi_store = effective_store is None
    product_sales = await product_sales_since(
        db,
        since=since,
        until=period_until,
        store_id=effective_store,
        limit=20 if multi_store else 15,
        by_store=multi_store,
    )

    return success(
        {
            "period": period,
            "store_id": effective_store,
            "store_name": store_name,
            "multi_store": multi_store,
            "member_total": await registered_member_count(db, store_id=effective_store),
            "visitors": await visitor_count_since(db, since=since, store_id=effective_store)
            if period == "today"
            else None,
            "summary": summary,
            "daily_series": await daily_series(db, days=7, store_id=effective_store),
            "stores": stores,
            "product_sales": product_sales,
            "payments": await payment_channel_stats(
                db, since=since, until=period_until, store_id=effective_store
            ),
            "delivery": await delivery_kpi(
                db, since=since, until=period_until, store_id=effective_store
            ),
        },
        request=request,
    )
