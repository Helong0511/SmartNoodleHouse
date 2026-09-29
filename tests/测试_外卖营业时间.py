"""外卖门店营业时间：多时段 / 跨午夜 / 边界 / 昨天覆盖 / 自动开关店。"""
from __future__ import annotations

import itertools
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import PlatformStoreBinding, Store
from app.services.store_hours_service import (
    StoreHoursService,
    desired_open_status,
    is_open_at,
    shipping_time_for_day,
)


def _row(weekday: int, oh: str, ch: str, *, cross: bool = False, is_open: bool = True):
    hh, mm = (int(x) for x in oh.split(":"))
    hh2, mm2 = (int(x) for x in ch.split(":"))
    return SimpleNamespace(
        weekday=weekday,
        slot_index=0,
        open_time=time(hh, mm),
        close_time=time(hh2, mm2),
        cross_day=cross,
        is_open=is_open,
    )


# 2024-01-01 为周一（模型 weekday=1），2024-01-07 为周日（weekday=7）
周一10点 = datetime(2024, 1, 1, 10, 0)
周一8点 = datetime(2024, 1, 1, 8, 0)
周一9点整 = datetime(2024, 1, 1, 9, 0)
周一18点整 = datetime(2024, 1, 1, 18, 0)
周一1点 = datetime(2024, 1, 1, 1, 0)
周一3点 = datetime(2024, 1, 1, 3, 0)
周日23点 = datetime(2024, 1, 7, 23, 0)


def test_单时段边界_到点开_到点关():
    rows = [_row(1, "09:00", "18:00")]
    assert is_open_at(rows, 周一9点整) is True      # open 边界即营业
    assert is_open_at(rows, 周一10点) is True
    assert is_open_at(rows, 周一18点整) is False    # close 边界即休息
    assert is_open_at(rows, 周一8点) is False


def test_多时段_中间休息():
    rows = [_row(1, "07:00", "09:00"), SimpleNamespace(
        weekday=1, slot_index=1, open_time=time(11, 30), close_time=time(19, 0),
        cross_day=False, is_open=True,
    )]
    assert is_open_at(rows, datetime(2024, 1, 1, 8, 0)) is True
    assert is_open_at(rows, datetime(2024, 1, 1, 10, 0)) is False
    assert is_open_at(rows, datetime(2024, 1, 1, 12, 0)) is True


def test_跨午夜_当天深夜与次日凌晨均覆盖():
    # 周日 22:00 营业到次日（周一）02:00
    rows = [_row(7, "22:00", "02:00")]
    assert is_open_at(rows, 周日23点) is True       # 当天深夜
    assert is_open_at(rows, 周一1点) is True        # 次日凌晨（昨天时段覆盖）
    assert is_open_at(rows, 周一3点) is False       # 已打烊


def test_停用时段与无配置():
    assert is_open_at([_row(1, "00:00", "23:59", is_open=False)], 周一10点) is False
    # 完全没有配置 → None，系统不自动控制
    assert desired_open_status([], 周一10点) is None
    assert desired_open_status([_row(1, "11:30", "14:00")], 周一10点) == "REST"
    assert desired_open_status([_row(1, "09:00", "18:00")], 周一10点) == "OPEN"


def test_配送时段串按序拼接():
    rows = [
        SimpleNamespace(weekday=1, slot_index=1, open_time=time(11, 30),
                        close_time=time(19, 0), is_open=True),
        SimpleNamespace(weekday=1, slot_index=0, open_time=time(7, 0),
                        close_time=time(9, 0), is_open=True),
        SimpleNamespace(weekday=2, slot_index=0, open_time=time(7, 0),
                        close_time=time(9, 0), is_open=True),
    ]
    assert shipping_time_for_day(rows, 1) == "7:00-9:00,11:30-19:00"
    assert shipping_time_for_day(rows, 3) == ""


@pytest.mark.asyncio
async def test_set_hours_全量覆盖(db_session):
    sid = (await _make_store(db_session, "S-OH-1")).id
    await StoreHoursService.set_hours(db_session, sid, [
        {"weekday": 1, "is_open": True, "slots": [
            {"open_time": "07:00", "close_time": "09:00"},
            {"open_time": "11:30", "close_time": "19:00"},
        ]},
        {"weekday": 7, "is_open": True, "slots": [
            {"open_time": "22:00", "close_time": "02:00"},
        ]},
    ])
    payload = await StoreHoursService.get_hours_payload(db_session, sid)
    周一 = next(d for d in payload if d["weekday"] == 1)
    周日 = next(d for d in payload if d["weekday"] == 7)
    assert len(周一["slots"]) == 2
    assert 周日["slots"][0]["cross_day"] is True

    # 全量覆盖：改成仅周一营业后，周日行应被删除
    await StoreHoursService.set_hours(db_session, sid, [
        {"weekday": 1, "is_open": True, "slots": [{"open_time": "09:00", "close_time": "18:00"}]},
        {"weekday": 7, "is_open": False, "slots": []},
    ])
    rows = await StoreHoursService.list_hours(db_session, sid)
    assert {r.weekday for r in rows} == {1}


@pytest.mark.asyncio
async def test_自动开关店_按营业时间切换并回写状态(db_session, monkeypatch):
    store = await _make_store(db_session, "S-OH-2")
    # 周一 09:00-18:00
    await StoreHoursService.set_hours(db_session, store.id, [
        {"weekday": 1, "is_open": True, "slots": [{"open_time": "09:00", "close_time": "18:00"}]},
    ])
    binding = PlatformStoreBinding(
        id=next(_seq), store_id=store.id, platform="MEITUAN", platform_shop_id="shop-1",
        status=1, auto_sync_open=True, platform_open_status=None,
    )
    # 第二个门店开启自动开关但未配置营业时间 → 应 skipped，不应调用平台
    store2 = await _make_store(db_session, "S-OH-3")
    binding2 = PlatformStoreBinding(
        id=next(_seq), store_id=store2.id, platform="ELEME", platform_shop_id="shop-2",
        status=1, auto_sync_open=True, platform_open_status=None,
    )
    db_session.add_all([binding, binding2])
    await db_session.commit()

    fake = MagicMock()
    fake.set_store_open_status = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.delivery_providers.get_delivery_provider",
        lambda platform, creds: fake,
    )

    async def _fake_creds(db, platform, store_id):
        return MagicMock()

    monkeypatch.setattr("app.services.delivery_context.get_credentials_for_store", _fake_creds)

    # 周一 10:00 营业中：OPEN
    monkeypatch.setattr("app.services.store_hours_service.naive_local_now", lambda: 周一10点)
    # apply_auto_open_tick 内部 from app.core.database import AsyncSessionLocal 局部导入，
    # patch 全局 AsyncSessionLocal 让它走测试的 in-memory session
    from app.core import database as _db_mod

    class _Ctx:
        def __init__(self):
            self._db = db_session

        async def __aenter__(self):
            return self._db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(_db_mod, "AsyncSessionLocal", _Ctx)
    stats = await StoreHoursService.apply_auto_open_tick(db_session)
    assert stats["opened"] == 1 and stats["closed"] == 0 and stats["skipped"] == 1
    assert binding.platform_open_status == "OPEN"
    assert binding.last_sync_at is not None
    # 未配置门店的平台绝不应被调用（仅 shop-1 一次）
    assert fake.set_store_open_status.await_count == 1
    assert fake.set_store_open_status.await_args.args[0] == "OPEN"

    # 周一 08:00 休息：应关店并回传当天配送时段串
    monkeypatch.setattr("app.services.store_hours_service.naive_local_now", lambda: 周一8点)
    stats = await StoreHoursService.apply_auto_open_tick(db_session)
    assert stats["closed"] == 1 and stats["opened"] == 0
    args = fake.set_store_open_status.await_args.args
    assert args[0] == "REST" and args[1] == "9:00-18:00"
    assert binding.platform_open_status == "REST"


_seq = itertools.count(1)


async def _make_store(db, name: str) -> Store:
    store = Store(id=next(_seq), store_no=name, name=name, status=1)
    db.add(store)
    await db.flush()
    return store
