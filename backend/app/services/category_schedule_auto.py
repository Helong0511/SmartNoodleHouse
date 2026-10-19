"""分类时段排序：按该时段历史销量自动推荐 / 刷新排序。

销量越多，销量序权重越高（相对默认序/上次手工序），排序越贴近真实点餐偏好。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import naive_local_now, now_local, serialize_dt
from app.models import (
    CategorySortSchedule,
    CategorySortScheduleItem,
    Order,
    OrderItem,
    Product,
    ProductCategory,
    Store,
)
from app.services.category_schedule import time_in_window
from app.services.recommendation_service import invalidate_store_caches
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

_PAID_ORDER = ("PAID", "PARTIAL_REFUND")


async def category_sort_auto_cfg(db: AsyncSession) -> dict[str, Any]:
    enabled = await SystemSettingsService.get_bool(db, "night_batch_category_sort_enabled", True)
    lookback = await SystemSettingsService.get_int(db, "night_batch_category_sort_lookback_days", 30)
    soft_qty = await SystemSettingsService.get_int(db, "night_batch_category_sort_soft_qty", 100)
    return {
        "enabled": enabled,
        "lookback_days": max(1, min(180, int(lookback))),
        # 该时段累计销量达到 soft_qty 时，完全按销量排序；不足则与先验序加权混合
        "soft_qty": max(1, min(100000, int(soft_qty))),
    }


def _as_local_time(paid_at: datetime | None) -> time | None:
    if paid_at is None:
        return None
    if paid_at.tzinfo is not None:
        paid_at = paid_at.astimezone().replace(tzinfo=None)
    return paid_at.time().replace(tzinfo=None)


def blend_category_order(
    categories: list[ProductCategory],
    sales_qty: dict[int, int],
    prior_order: dict[int, int],
    *,
    soft_qty: int,
) -> tuple[list[ProductCategory], float, int]:
    """销量与先验序加权混合。返回 (排序后分类, 销量权重 0~1, 时段累计销量)。"""
    if not categories:
        return [], 0.0, 0
    total = int(sum(max(0, int(v)) for v in sales_qty.values()))
    weight = min(1.0, total / float(soft_qty)) if soft_qty > 0 else 1.0

    by_sales = sorted(
        categories,
        key=lambda c: (
            -int(sales_qty.get(int(c.id), 0)),
            int(prior_order.get(int(c.id), c.sort_order or 0)),
            int(c.id),
        ),
    )
    sales_rank = {int(c.id): i for i, c in enumerate(by_sales)}

    by_prior = sorted(
        categories,
        key=lambda c: (
            int(prior_order.get(int(c.id), c.sort_order or 0)),
            int(c.id),
        ),
    )
    prior_rank = {int(c.id): i for i, c in enumerate(by_prior)}

    def score(c: ProductCategory) -> tuple[float, int]:
        cid = int(c.id)
        blended = weight * sales_rank[cid] + (1.0 - weight) * prior_rank[cid]
        return (blended, cid)

    ordered = sorted(categories, key=score)
    return ordered, float(weight), total


async def load_slot_category_sales(
    db: AsyncSession,
    *,
    store_id: int,
    start_time: time,
    end_time: time,
    since: datetime,
) -> dict[int, int]:
    """统计门店在 [start,end) 时段内、since 起已支付订单的分类销量（件数）。"""
    sid = int(store_id)
    start_t = start_time.replace(tzinfo=None) if hasattr(start_time, "replace") else start_time
    end_t = end_time.replace(tzinfo=None) if hasattr(end_time, "replace") else end_time

    rows = (
        await db.execute(
            select(Product.category_id, Order.paid_at, OrderItem.quantity)
            .join(OrderItem, OrderItem.product_id == Product.id)
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Product.store_id == sid,
                Product.status == "ON_SALE",
                Order.store_id == sid,
                Order.payment_status.in_(_PAID_ORDER),
                Order.paid_at.is_not(None),
                Order.paid_at >= since,
            )
        )
    ).all()

    sales: dict[int, int] = defaultdict(int)
    for cat_id, paid_at, qty in rows:
        if cat_id is None:
            continue
        t = _as_local_time(paid_at)
        if t is None or not time_in_window(t, start_t, end_t):
            continue
        sales[int(cat_id)] += int(qty or 0)
    return dict(sales)


async def refresh_schedule_auto_sort(
    db: AsyncSession,
    schedule: CategorySortSchedule,
    *,
    lookback_days: int,
    soft_qty: int,
    since: datetime | None = None,
) -> dict[str, Any]:
    """按销量刷新单个方案的分类序；写回 items 并标记 auto_updated_at。"""
    sid = int(schedule.store_id)
    since_dt = since or (naive_local_now() - timedelta(days=int(lookback_days)))

    cats = list(
        (
            await db.execute(
                select(ProductCategory)
                .where(
                    ProductCategory.store_id == sid,
                    ProductCategory.status == 1,
                )
                .order_by(ProductCategory.sort_order, ProductCategory.id)
            )
        ).scalars().all()
    )
    if not cats:
        return {
            "schedule_id": int(schedule.id),
            "store_id": sid,
            "skipped": True,
            "reason": "no_categories",
        }

    existing = list(
        (
            await db.execute(
                select(CategorySortScheduleItem).where(
                    CategorySortScheduleItem.schedule_id == int(schedule.id)
                )
            )
        ).scalars().all()
    )
    prior: dict[int, int] = {
        int(c.id): int(c.sort_order or 0) for c in cats
    }
    for it in existing:
        prior[int(it.category_id)] = int(it.sort_order)

    sales = await load_slot_category_sales(
        db,
        store_id=sid,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        since=since_dt,
    )
    ordered, weight, total_qty = blend_category_order(
        cats, sales, prior, soft_qty=int(soft_qty)
    )

    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.schedule_id == int(schedule.id)
        )
    )
    # SQLite 测试库 BIGINT 主键无自增，显式分配 id；MySQL 显式 id 亦安全
    max_id = int(
        (await db.execute(select(func.max(CategorySortScheduleItem.id)))).scalar() or 0
    )
    for i, cat in enumerate(ordered):
        max_id += 1
        db.add(
            CategorySortScheduleItem(
                id=max_id,
                schedule_id=int(schedule.id),
                category_id=int(cat.id),
                sort_order=i,
            )
        )
    schedule.auto_updated_at = now_local()
    await db.flush()
    await invalidate_store_caches(sid)

    return {
        "schedule_id": int(schedule.id),
        "store_id": sid,
        "name": schedule.name,
        "skipped": False,
        "total_qty": total_qty,
        "confidence": round(weight, 4),
        "lookback_days": int(lookback_days),
        "soft_qty": int(soft_qty),
        "order": [{"category_id": int(c.id), "name": c.name, "qty": int(sales.get(int(c.id), 0))} for c in ordered],
        "auto_updated_at": serialize_dt(schedule.auto_updated_at) if schedule.auto_updated_at else None,
    }


async def refresh_store_category_schedules(
    db: AsyncSession,
    store_id: int,
    *,
    lookback_days: int,
    soft_qty: int,
    only_auto: bool = True,
    schedule_id: int | None = None,
) -> dict[str, Any]:
    """刷新门店下启用中的（自动）时段方案。"""
    sid = int(store_id)
    q = select(CategorySortSchedule).where(
        CategorySortSchedule.store_id == sid,
        CategorySortSchedule.status == 1,
    )
    if only_auto:
        q = q.where(CategorySortSchedule.auto_sort == 1)
    if schedule_id is not None:
        q = q.where(CategorySortSchedule.id == int(schedule_id))
    schedules = list((await db.execute(q.order_by(CategorySortSchedule.id))).scalars().all())
    if not schedules:
        return {
            "store_id": sid,
            "updated": 0,
            "schedules": [],
            "skipped": True,
            "reason": "no_auto_schedules",
        }

    since = naive_local_now() - timedelta(days=int(lookback_days))
    results = []
    for sched in schedules:
        try:
            results.append(
                await refresh_schedule_auto_sort(
                    db,
                    sched,
                    lookback_days=lookback_days,
                    soft_qty=soft_qty,
                    since=since,
                )
            )
        except Exception:
            logger.exception(
                "分类时段自动排序失败 store_id=%s schedule_id=%s", sid, sched.id
            )
            results.append(
                {
                    "schedule_id": int(sched.id),
                    "store_id": sid,
                    "error": True,
                }
            )
    return {
        "store_id": sid,
        "updated": sum(1 for r in results if not r.get("skipped") and not r.get("error")),
        "schedules": results,
    }


async def refresh_all_category_schedule_auto_sort(
    db: AsyncSession,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """全店夜间批：刷新所有 auto_sort=1 的启用方案。"""
    cfg = cfg or await category_sort_auto_cfg(db)
    if not cfg.get("enabled", True):
        return {"skipped": "disabled", "stores": []}
    store_ids = list((await db.execute(select(Store.id).order_by(Store.id))).scalars().all())
    stores_out = []
    for sid in store_ids:
        try:
            stores_out.append(
                await refresh_store_category_schedules(
                    db,
                    int(sid),
                    lookback_days=cfg["lookback_days"],
                    soft_qty=cfg["soft_qty"],
                    only_auto=True,
                )
            )
            # 按店 commit：单店失败不拖垮已完成门店
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("分类时段自动排序门店失败 store_id=%s", sid)
            stores_out.append({"store_id": int(sid), "error": True})
    return {
        "lookback_days": cfg["lookback_days"],
        "soft_qty": cfg["soft_qty"],
        "stores": stores_out,
        "updated_schedules": sum(int(s.get("updated") or 0) for s in stores_out if isinstance(s, dict)),
    }
