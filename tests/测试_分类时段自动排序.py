"""分类时段销量自动排序：加权混合与按时段聚合。"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models import CategorySortSchedule, CategorySortScheduleItem, OrderItem
from app.services.category_schedule_auto import (
    blend_category_order,
    refresh_schedule_auto_sort,
)
from tests.reco_factories import (
    _SCHEDULE_ID,
    _SCHEDULE_ITEM_ID,
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
)


def test_blend_zero_sales_keeps_prior():
    cats = [
        SimpleNamespace(id=1, name="早点", sort_order=0),
        SimpleNamespace(id=2, name="面", sort_order=1),
        SimpleNamespace(id=3, name="饮品", sort_order=2),
    ]
    ordered, weight, total = blend_category_order(
        cats, {}, {1: 0, 2: 1, 3: 2}, soft_qty=100
    )
    assert weight == 0.0
    assert total == 0
    assert [c.id for c in ordered] == [1, 2, 3]


def test_blend_full_sales_follows_qty():
    cats = [
        SimpleNamespace(id=1, name="早点", sort_order=0),
        SimpleNamespace(id=2, name="面", sort_order=1),
        SimpleNamespace(id=3, name="饮品", sort_order=2),
    ]
    ordered, weight, total = blend_category_order(
        cats,
        {3: 80, 2: 50, 1: 10},
        {1: 0, 2: 1, 3: 2},
        soft_qty=100,
    )
    assert weight == 1.0
    assert total == 140
    assert [c.id for c in ordered] == [3, 2, 1]


def test_blend_partial_weight_between_prior_and_sales():
    cats = [
        SimpleNamespace(id=1, name="A", sort_order=0),
        SimpleNamespace(id=2, name="B", sort_order=1),
    ]
    ordered, weight, total = blend_category_order(
        cats, {2: 50}, {1: 0, 2: 1}, soft_qty=100
    )
    assert weight == 0.5
    assert total == 50
    assert [c.id for c in ordered] == [1, 2]


@pytest.mark.asyncio
async def test_refresh_schedule_auto_sort_by_slot_sales(db_session, monkeypatch):
    enqueue_invalidation = AsyncMock()
    monkeypatch.setattr(
        "app.services.category_schedule_auto.invalidate_store_menu_cache",
        enqueue_invalidation,
    )
    db = db_session
    store = await make_store(db)
    customer = await make_customer(db)
    c_breakfast = await make_category(db, store.id, name="早点", sort_order=0)
    c_noodle = await make_category(db, store.id, name="面", sort_order=1)
    p_b = await make_product(db, store.id, c_breakfast.id, name="豆浆")
    p_n = await make_product(db, store.id, c_noodle.id, name="牛肉面")

    sched = CategorySortSchedule(
        id=next(_SCHEDULE_ID),
        store_id=store.id,
        name="午市",
        start_time=time(11, 0),
        end_time=time(14, 0),
        priority=5,
        status=1,
        auto_sort=1,
    )
    db.add(sched)
    await db.flush()
    db.add_all(
        [
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=sched.id,
                category_id=c_breakfast.id,
                sort_order=0,
            ),
            CategorySortScheduleItem(
                id=next(_SCHEDULE_ITEM_ID),
                schedule_id=sched.id,
                category_id=c_noodle.id,
                sort_order=1,
            ),
        ]
    )
    await db.flush()

    # 午市：面 20 + 早点 2；早餐订单大量豆浆不应计入午市
    # paid_at 相对「本地现在」锚定，避免写死日历日随 lookback 窗口漂移
    from app.core.response import naive_local_now

    day = (naive_local_now() - timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    lunch = await make_paid_order(
        db,
        customer.id,
        store.id,
        [(p_n, 20, []), (p_b, 2, [])],
        days_ago=2,
    )
    lunch.paid_at = day.replace(hour=12, minute=30)
    breakfast = await make_paid_order(
        db,
        customer.id,
        store.id,
        [(p_b, 100, [])],
        days_ago=2,
    )
    breakfast.paid_at = day.replace(hour=8, minute=0)
    await db.flush()

    out = await refresh_schedule_auto_sort(db, sched, lookback_days=30, soft_qty=10)
    assert out["skipped"] is False
    assert out["total_qty"] == 22
    assert out["confidence"] == 1.0
    assert [row["category_id"] for row in out["order"]] == [c_noodle.id, c_breakfast.id]

    items = list(
        (
            await db.execute(
                select(CategorySortScheduleItem).where(
                    CategorySortScheduleItem.schedule_id == sched.id
                )
            )
        ).scalars().all()
    )
    ordered = sorted(items, key=lambda x: x.sort_order)
    assert [it.category_id for it in ordered] == [c_noodle.id, c_breakfast.id]
    assert sched.auto_updated_at is not None
    enqueue_invalidation.assert_awaited_once_with(db, store.id)
