"""关店夜间批处理：窗口判定、销量热销/推荐标记。"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.night_batch_service import (
    in_night_window,
    refresh_sales_flags_for_store,
    run_night_batch,
)
from app.services.scheduler_admin import build_trigger, format_trigger_label


def test_in_night_window_default_beijing():
    assert in_night_window(datetime(2026, 9, 23, 2, 0), start_hour=2, end_hour=5)
    assert in_night_window(datetime(2026, 9, 23, 4, 59), start_hour=2, end_hour=5)
    assert not in_night_window(datetime(2026, 9, 23, 5, 0), start_hour=2, end_hour=5)
    assert not in_night_window(datetime(2026, 9, 23, 14, 0), start_hour=2, end_hour=5)


def test_in_night_window_cross_midnight():
    assert in_night_window(datetime(2026, 9, 23, 23, 30), start_hour=23, end_hour=4)
    assert in_night_window(datetime(2026, 9, 24, 1, 0), start_hour=23, end_hour=4)
    assert not in_night_window(datetime(2026, 9, 23, 12, 0), start_hour=23, end_hour=4)


def test_cron_trigger_uses_shanghai_timezone():
    trig = build_trigger({"type": "cron", "hour": 2, "minute": 10})
    assert "Asia/Shanghai" in str(getattr(trig, "timezone", "") or trig)
    assert "业务时区" in format_trigger_label({"type": "cron", "hour": 2, "minute": 10})


def test_task_catalog_has_night_batch():
    from app.tasks.scheduler import TASK_CATALOG

    ids = {m["id"] for m in TASK_CATALOG}
    assert "night_data_batch" in ids
    meta = next(m for m in TASK_CATALOG if m["id"] == "night_data_batch")
    assert meta["default_schedule"]["hour"] == 2
    assert meta["default_schedule"]["minute"] == 10


@pytest.mark.asyncio
async def test_run_night_batch_skips_outside_window():
    db = MagicMock()
    with patch(
        "app.services.night_batch_service._cfg",
        AsyncMock(
            return_value={
                "enabled": True,
                "start_hour": 2,
                "end_hour": 5,
                "sales_lookback_days": 7,
                "hot_top_n": 5,
                "recommend_top_n": 8,
                "taste_customer_limit": 2000,
                "taste_lookback_days": 3,
                "category_sort_enabled": True,
                "category_sort_lookback_days": 30,
                "category_sort_soft_qty": 100,
            }
        ),
    ):
        out = await run_night_batch(db, now=datetime(2026, 9, 23, 14, 0))
    assert out["ran"] is False
    assert out["skipped"] == "outside_window"


@pytest.mark.asyncio
async def test_run_night_batch_skips_when_disabled():
    db = MagicMock()
    with patch(
        "app.services.night_batch_service._cfg",
        AsyncMock(
            return_value={
                "enabled": False,
                "start_hour": 2,
                "end_hour": 5,
                "sales_lookback_days": 7,
                "hot_top_n": 5,
                "recommend_top_n": 8,
                "taste_customer_limit": 2000,
                "taste_lookback_days": 3,
                "category_sort_enabled": True,
                "category_sort_lookback_days": 30,
                "category_sort_soft_qty": 100,
            }
        ),
    ):
        out = await run_night_batch(db, now=datetime(2026, 9, 23, 3, 0))
    assert out["ran"] is False
    assert out["skipped"] == "disabled"


@pytest.mark.asyncio
async def test_refresh_sales_flags_marks_top_products():
    """夜间批仅刷新 is_hot；is_recommended 保持人工勾选不被覆盖。"""
    p1 = SimpleNamespace(id=1, is_hot=False, is_recommended=False)
    p2 = SimpleNamespace(id=2, is_hot=True, is_recommended=True)
    p3 = SimpleNamespace(id=3, is_hot=False, is_recommended=True)
    db = MagicMock()
    db.flush = AsyncMock()

    with (
        patch(
            "app.services.night_batch_service.rank_store_sales",
            AsyncMock(return_value=[(1, 100), (3, 50), (2, 10)]),
        ),
        patch(
            "app.services.night_batch_service.invalidate_store_caches",
            AsyncMock(),
        ),
    ):
        # execute → scalars → all for product list
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[p1, p2, p3])))
            )
        )
        out = await refresh_sales_flags_for_store(
            db,
            store_id=1,
            since=datetime.utcnow() - timedelta(days=7),
            hot_top_n=1,
            recommend_top_n=2,
        )
    assert out["skipped"] is False
    assert out["hot_ids"] == [1]
    assert out["recommend_ids"] == []
    assert p1.is_hot is True and p1.is_recommended is False
    assert p3.is_hot is False and p3.is_recommended is True  # 人工推荐保留
    assert p2.is_hot is False and p2.is_recommended is True  # 人工推荐保留
    assert out["changed"] == 2  # 仅 p1 变热销、p2 取消热销


@pytest.mark.asyncio
async def test_refresh_sales_flags_no_sales_leaves_untouched():
    db = MagicMock()
    with patch(
        "app.services.night_batch_service.rank_store_sales",
        AsyncMock(return_value=[]),
    ):
        out = await refresh_sales_flags_for_store(
            db,
            store_id=1,
            since=datetime.utcnow() - timedelta(days=7),
            hot_top_n=5,
            recommend_top_n=8,
        )
    assert out["skipped"] is True
    assert out["reason"] == "no_sales"


@pytest.mark.asyncio
async def test_night_batch_settings_in_meta():
    from app.services.system_settings_service import BUSINESS_SETTING_META, PLATFORM_ONLY_KEYS

    for key in (
        "night_batch_enabled",
        "night_batch_window_start_hour",
        "night_batch_window_end_hour",
        "night_batch_sales_lookback_days",
        "night_batch_hot_top_n",
        "night_batch_recommend_top_n",
        "night_batch_taste_customer_limit",
        "night_batch_taste_lookback_days",
        "night_batch_category_sort_enabled",
        "night_batch_category_sort_lookback_days",
        "night_batch_category_sort_soft_qty",
    ):
        assert key in BUSINESS_SETTING_META
        assert key in PLATFORM_ONLY_KEYS
