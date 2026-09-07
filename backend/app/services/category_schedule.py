"""分类分时段排序：命中方案后覆盖默认 sort_order。"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import CategorySortSchedule, CategorySortScheduleItem, ProductCategory


def parse_hhmm(value: Any) -> time:
    """解析 'HH:MM' / 'HH:MM:SS' / time / datetime.time。"""
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.time().replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        raise ValueError("时间不能为空")
    parts = text.split(":")
    if len(parts) < 2:
        raise ValueError(f"时间格式无效: {value}")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return time(hour=h, minute=m, second=s)


def time_in_window(now_t: time, start: time, end: time) -> bool:
    """[start, end) ；若 start>end 表示跨午夜。start==end 视为全天。"""
    if start == end:
        return True
    if start < end:
        return start <= now_t < end
    return now_t >= start or now_t < end


def pick_active_schedule(
    schedules: Sequence[CategorySortSchedule],
    now_t: time,
) -> CategorySortSchedule | None:
    matched = [s for s in schedules if time_in_window(now_t, s.start_time, s.end_time)]
    if not matched:
        return None
    matched.sort(key=lambda s: (-int(s.priority or 0), -int(s.id)))
    return matched[0]


async def load_enabled_schedules(db: AsyncSession, store_id: int) -> list[CategorySortSchedule]:
    rows = (
        await db.execute(
            select(CategorySortSchedule)
            .where(
                CategorySortSchedule.store_id == int(store_id),
                CategorySortSchedule.status == 1,
            )
            .order_by(CategorySortSchedule.priority.desc(), CategorySortSchedule.id.desc())
        )
    ).scalars().all()
    return list(rows)


async def load_schedule_item_orders(
    db: AsyncSession, schedule_id: int
) -> dict[int, int]:
    rows = (
        await db.execute(
            select(CategorySortScheduleItem).where(
                CategorySortScheduleItem.schedule_id == int(schedule_id)
            )
        )
    ).scalars().all()
    return {int(r.category_id): int(r.sort_order) for r in rows}


def sort_categories_by_schedule(
    cats: list[ProductCategory],
    item_orders: dict[int, int],
) -> list[ProductCategory]:
    """方案内分类按 item sort_order；未列入的跟在后面，按默认 sort_order。"""

    def key(c: ProductCategory):
        cid = int(c.id)
        if cid in item_orders:
            return (0, item_orders[cid], cid)
        return (1, int(c.sort_order or 0), cid)

    return sorted(cats, key=key)


async def apply_category_schedule_sort(
    db: AsyncSession,
    cats: list[ProductCategory],
    *,
    store_id: int | None,
    at: time | None = None,
) -> tuple[list[ProductCategory], CategorySortSchedule | None]:
    """对同一门店分类套用当前时段排序。多门店混合列表不处理。"""
    if not cats or store_id is None:
        return cats, None
    if any(int(c.store_id) != int(store_id) for c in cats):
        return cats, None
    now_t = at or now_local().time().replace(tzinfo=None)
    schedules = await load_enabled_schedules(db, int(store_id))
    active = pick_active_schedule(schedules, now_t)
    if not active:
        return sorted(cats, key=lambda c: (int(c.sort_order or 0), int(c.id))), None
    orders = await load_schedule_item_orders(db, int(active.id))
    return sort_categories_by_schedule(cats, orders), active
