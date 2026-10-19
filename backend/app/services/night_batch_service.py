"""关店时段（默认北京时间 02:00–05:00）慢任务批处理。

面向销量热销/店长推荐位刷新、餐品口味画像整店重算、顾客智能口味深度学习等
需要扫全量/多日数据的工作，避开营业高峰。增量学习仍由白天短周期任务兜底。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import naive_local_now, now_local
from app.models import Order, OrderItem, Product, Store
from app.services.recommendation_service import invalidate_store_caches
from app.services.system_settings_service import SystemSettingsService
from app.services.taste_learning_service import TasteLearningService
from app.services.taste_profile_service import recompute_store

logger = logging.getLogger(__name__)

_PAID_ORDER = ("PAID", "PARTIAL_REFUND")


async def _cfg(db: AsyncSession) -> dict[str, Any]:
    enabled = await SystemSettingsService.get_bool(db, "night_batch_enabled", True)
    start_h = await SystemSettingsService.get_int(db, "night_batch_window_start_hour", 2)
    end_h = await SystemSettingsService.get_int(db, "night_batch_window_end_hour", 5)
    lookback = await SystemSettingsService.get_int(db, "night_batch_sales_lookback_days", 7)
    hot_n = await SystemSettingsService.get_int(db, "night_batch_hot_top_n", 5)
    reco_n = await SystemSettingsService.get_int(db, "night_batch_recommend_top_n", 8)
    cust_limit = await SystemSettingsService.get_int(db, "night_batch_taste_customer_limit", 2000)
    taste_days = await SystemSettingsService.get_int(db, "night_batch_taste_lookback_days", 3)
    from app.services.category_schedule_auto import category_sort_auto_cfg

    cat_sort = await category_sort_auto_cfg(db)
    return {
        "enabled": enabled,
        "start_hour": max(0, min(23, int(start_h))),
        "end_hour": max(0, min(24, int(end_h))),
        "sales_lookback_days": max(1, min(90, int(lookback))),
        "hot_top_n": max(0, min(50, int(hot_n))),
        "recommend_top_n": max(0, min(50, int(reco_n))),
        "taste_customer_limit": max(50, min(20000, int(cust_limit))),
        "taste_lookback_days": max(1, min(30, int(taste_days))),
        "category_sort_enabled": bool(cat_sort["enabled"]),
        "category_sort_lookback_days": cat_sort["lookback_days"],
        "category_sort_soft_qty": cat_sort["soft_qty"],
    }


def in_night_window(now: datetime | None = None, *, start_hour: int = 2, end_hour: int = 5) -> bool:
    """业务时区当前时刻是否落在 [start, end) 关店窗口。支持跨午夜（如 23→4）。"""
    dt = now or now_local()
    hour = int(dt.hour)
    start = int(start_hour)
    end = int(end_hour)
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def rank_store_sales(
    db: AsyncSession,
    *,
    store_id: int,
    since: datetime,
    limit: int,
) -> list[tuple[int, int]]:
    """返回 [(product_id, qty), ...] 按销量降序。"""
    if limit <= 0:
        return []
    q = (
        select(
            Product.id,
            func.coalesce(func.sum(OrderItem.quantity), 0).label("sales"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Product.store_id == int(store_id),
            Product.status == "ON_SALE",
            Order.store_id == int(store_id),
            Order.payment_status.in_(_PAID_ORDER),
            Order.paid_at >= since,
        )
        .group_by(Product.id)
        .having(func.sum(OrderItem.quantity) > 0)
        .order_by(func.sum(OrderItem.quantity).desc(), Product.id.asc())
        .limit(int(limit))
    )
    rows = (await db.execute(q)).all()
    return [(int(pid), int(qty)) for pid, qty in rows]


async def refresh_sales_flags_for_store(
    db: AsyncSession,
    *,
    store_id: int,
    since: datetime,
    hot_top_n: int,
    recommend_top_n: int = 0,
) -> dict[str, Any]:
    """按近 N 日销量刷新本店 is_hot（热销）。

    is_recommended（店长推荐）改为仅人工勾选，夜间批不再覆盖。
    recommend_top_n 保留入参兼容旧调用，已忽略。
    """
    del recommend_top_n  # 兼容旧签名；推荐标记不再按销量自动改写
    need = max(hot_top_n, 1)
    ranked = await rank_store_sales(db, store_id=store_id, since=since, limit=need)
    if not ranked:
        return {
            "store_id": store_id,
            "skipped": True,
            "reason": "no_sales",
            "hot_ids": [],
            "recommend_ids": [],
        }

    hot_ids = {pid for pid, _ in ranked[:hot_top_n]} if hot_top_n else set()

    products = list(
        (
            await db.execute(
                select(Product).where(
                    Product.store_id == int(store_id),
                    Product.status == "ON_SALE",
                )
            )
        ).scalars().all()
    )
    changed = 0
    for p in products:
        want_hot = p.id in hot_ids
        if bool(p.is_hot) != want_hot:
            p.is_hot = want_hot
            changed += 1
    if changed:
        await db.flush()
        await invalidate_store_caches(int(store_id))
    return {
        "store_id": store_id,
        "skipped": False,
        "changed": changed,
        "hot_ids": sorted(hot_ids),
        "recommend_ids": [],
        "ranked": [{"product_id": pid, "qty": qty} for pid, qty in ranked[:need]],
    }


async def refresh_all_sales_flags(db: AsyncSession, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or await _cfg(db)
    since = naive_local_now() - timedelta(days=cfg["sales_lookback_days"])
    store_ids = list((await db.execute(select(Store.id).order_by(Store.id))).scalars().all())
    results = []
    for sid in store_ids:
        try:
            results.append(
                await refresh_sales_flags_for_store(
                    db,
                    store_id=int(sid),
                    since=since,
                    hot_top_n=cfg["hot_top_n"],
                    recommend_top_n=cfg["recommend_top_n"],
                )
            )
            # 按店 commit：一店失败/崩溃不拖垮已完成门店
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("夜间销量标记刷新失败 store_id=%s", sid)
            results.append({"store_id": int(sid), "error": True})
    return {
        "lookback_days": cfg["sales_lookback_days"],
        "stores": results,
        "updated_stores": sum(1 for r in results if r.get("changed")),
    }


async def rebuild_all_product_taste_profiles(db: AsyncSession) -> dict[str, Any]:
    store_ids = list((await db.execute(select(Store.id).order_by(Store.id))).scalars().all())
    per_store = []
    total = 0
    for sid in store_ids:
        try:
            n = await recompute_store(db, int(sid))
            total += n
            await db.commit()
            await invalidate_store_caches(int(sid))
            per_store.append({"store_id": int(sid), "rebuilt": n})
        except Exception:
            await db.rollback()
            logger.exception("夜间餐品口味画像重算失败 store_id=%s", sid)
            per_store.append({"store_id": int(sid), "error": True})
    return {"products": total, "stores": per_store}


async def deep_learn_customer_tastes(db: AsyncSession, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """关店窗内深度学习：扫最近数日有行为的顾客，扩大单次 limit。"""
    cfg = cfg or await _cfg(db)
    since = naive_local_now() - timedelta(days=cfg["taste_lookback_days"])
    stats = await TasteLearningService.learn_recent_customers(
        db,
        since=since,
        limit=cfg["taste_customer_limit"],
    )
    return {
        "lookback_days": cfg["taste_lookback_days"],
        "customer_limit": cfg["taste_customer_limit"],
        **stats,
    }


async def run_night_batch(
    db: AsyncSession,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """关店窗编排入口。force=True 时忽略窗口（供管理端手动触发/测试）。"""
    cfg = await _cfg(db)
    clock = now or now_local()
    result: dict[str, Any] = {
        "enabled": cfg["enabled"],
        "window": f"{cfg['start_hour']:02d}:00-{cfg['end_hour']:02d}:00",
        "at": clock.isoformat(sep=" ", timespec="seconds"),
        "ran": False,
    }
    if not cfg["enabled"] and not force:
        result["skipped"] = "disabled"
        return result
    if not force and not in_night_window(
        clock, start_hour=cfg["start_hour"], end_hour=cfg["end_hour"]
    ):
        result["skipped"] = "outside_window"
        return result

    result["ran"] = True
    result["sales_flags"] = await refresh_all_sales_flags(db, cfg)
    if cfg.get("category_sort_enabled", True):
        from app.services.category_schedule_auto import refresh_all_category_schedule_auto_sort

        try:
            result["category_sort"] = await refresh_all_category_schedule_auto_sort(
                db,
                {
                    "enabled": True,
                    "lookback_days": cfg["category_sort_lookback_days"],
                    "soft_qty": cfg["category_sort_soft_qty"],
                },
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("夜间分类时段排序失败")
            result["category_sort"] = {"error": True}
    else:
        result["category_sort"] = {"skipped": "disabled"}
    result["product_taste"] = await rebuild_all_product_taste_profiles(db)
    try:
        result["customer_taste"] = await deep_learn_customer_tastes(db, cfg)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("夜间顾客口味深度学习失败")
        result["customer_taste"] = {"error": True}
    try:
        from app.services.lifecycle_service import LifecycleService

        result["lifecycle"] = await LifecycleService.refresh_all(db)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("夜间 lifecycle/RFM 刷新失败")
        result["lifecycle"] = {"error": True}
    return result
