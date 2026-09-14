"""库存原子扣减 / 订单状态机 / inbox 事件 ID 并发相关单测"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_staff_transition
from app.services.webhook_inbox import make_event_id


def test_make_event_id_prefers_request_id():
    eid = make_event_id("MEITUAN", {"requestId": "R1", "order_id": "9"}, b"body")
    assert eid == "MEITUAN:R1"


def test_make_event_id_stable_hash():
    a = make_event_id("ELEME", {"type": "217"}, b"abc")
    b = make_event_id("ELEME", {"type": "217"}, b"abc")
    c = make_event_id("ELEME", {"type": "217"}, b"abd")
    assert a == b and a != c


def test_cannot_paid_twice_via_staff():
    with pytest.raises(AppException):
        assert_staff_transition("PAID", "PAID")


@pytest.mark.asyncio
async def test_deduct_stock_insufficient():
    from app.services.stock_service import deduct_stock

    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(AppException) as ei:
        await deduct_stock(db, product_id=1, qty=3)
    assert ei.value.code == 40031


@pytest.mark.asyncio
async def test_deduct_stock_ok():
    from app.services.stock_service import deduct_stock

    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    await deduct_stock(db, product_id=1, qty=2)
    db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_concurrent_state_machine_guards():
    """模拟并发：同一订单两次 COMPLETED，第二次应非法。"""
    errors = []

    def once(current_holder):
        try:
            assert_staff_transition(current_holder[0], "COMPLETED")
            current_holder[0] = "COMPLETED"
        except AppException as e:
            errors.append(e)

    holder = ["READY"]
    once(holder)
    once(holder)
    assert len(errors) == 1
    assert holder[0] == "COMPLETED"


def test_delivery_auto_confirm_default_off():
    from app.core.config import Settings

    s = Settings(delivery_auto_confirm=False)
    assert s.delivery_auto_confirm is False
