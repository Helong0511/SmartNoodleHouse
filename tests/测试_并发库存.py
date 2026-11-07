"""库存原子扣减 / 订单状态机 / inbox 事件 ID 并发相关单测"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_staff_transition
from app.services.webhook_inbox import make_event_id


def test_make_event_id_prefers_request_id():
    eid = make_event_id("MEITUAN", {"requestId": "R1", "order_id": "9"}, b"body")
    assert eid == "MEITUAN:R1"


def test_make_event_id_stable_hash_uses_canonical_payload():
    # 已验签的结构化 payload 是事件身份来源；传输层 JSON 排版不应改变事件 ID。
    raw = {"type": "217", "order_id": "order-1"}
    a = make_event_id("ELEME", raw, b'{"type":"217","order_id":"order-1"}')
    b = make_event_id("ELEME", raw, b'{ "order_id": "order-1", "type": "217" }')
    changed = make_event_id(
        "ELEME", {"type": "217", "order_id": "order-1", "status": "DELIVERED"}, b"abc"
    )
    empty_a = make_event_id("ELEME", {}, b"abc")
    empty_b = make_event_id("ELEME", {}, b"abd")
    assert a == b
    assert a != changed
    # 无结构化 payload 时才回退到原始 body 指纹。
    assert empty_a != empty_b


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



@pytest.mark.asyncio
async def test_store_stock_cannot_override_master_product_off_shelf_status():
    """门店库存覆盖状态为在售，也不能绕过品牌餐品主档停售。"""
    from app.services.stock_service import deduct_stock

    db = AsyncMock()
    result = MagicMock()
    result.first.return_value = (7, "OFF_SALE")
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(AppException) as exc:
        await deduct_stock(db, product_id=101, qty=1, store_id=7)

    assert exc.value.code == 40031
    assert "品牌餐品已停售" in str(exc.value)
    # 只读取并锁定餐品主档；不得继续读取门店库存或执行扣减 UPDATE。
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_multi_item_stock_deduction_uses_stable_product_lock_order(monkeypatch):
    from app.services.stock_service import deduct_for_order_items

    calls = []

    async def fake_deduct(_db, product_id, qty, *, store_id=None):
        calls.append((product_id, qty, store_id))

    monkeypatch.setattr("app.services.stock_service.deduct_stock", fake_deduct)
    monkeypatch.setattr(
        "app.services.menu_cache_service.enqueue_menu_cache_invalidation", AsyncMock()
    )

    await deduct_for_order_items(
        AsyncMock(),
        [
            {"product_id": 9, "quantity": 1},
            {"product_id": 2, "quantity": 3},
            {"product_id": 9, "quantity": 2},
            {"product_id": 5, "quantity": 1},
        ],
        store_id=7,
    )

    assert calls == [(2, 3, 7), (5, 1, 7), (9, 3, 7)]


@pytest.mark.parametrize(
    "items",
    [
        [
            SimpleNamespace(product_id=9, quantity=1),
            SimpleNamespace(product_id=2, quantity=3),
            SimpleNamespace(product_id=9, quantity=2),
        ],
        [
            SimpleNamespace(product_id=2, quantity=3),
            SimpleNamespace(product_id=9, quantity=2),
            SimpleNamespace(product_id=9, quantity=1),
        ],
    ],
)
@pytest.mark.asyncio
async def test_order_restore_and_rededuct_use_stable_product_lock_order(monkeypatch, items):
    from app.services.order_lifecycle import _deduct_order_stock, _restore_order_stock
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    restore_calls = []
    deduct_calls = []

    async def fake_restore(_db, product_id, qty, *, store_id=None):
        restore_calls.append((product_id, qty, store_id))

    async def fake_deduct(_db, product_id, qty, *, store_id=None):
        deduct_calls.append((product_id, qty, store_id))

    monkeypatch.setattr("app.services.stock_service.restore_stock", fake_restore)
    monkeypatch.setattr("app.services.stock_service.deduct_stock", fake_deduct)
    monkeypatch.setattr(
        "app.services.menu_cache_service.enqueue_menu_cache_invalidation", AsyncMock()
    )
    order = SimpleNamespace(id=88, store_id=7)

    await _restore_order_stock(db, order)
    await _deduct_order_stock(db, order)

    expected = [(2, 3, 7), (9, 3, 7)]
    assert restore_calls == expected
    assert deduct_calls == expected

@pytest.mark.asyncio
async def test_store_stock_upsert_locks_product_before_store_override(monkeypatch):
    from app.api import products_stocks

    product = SimpleNamespace(id=101, store_id=7)
    row = SimpleNamespace(id=501, stock=4, status=1)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeDB:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement).lower())
            return Result(product if len(self.statements) == 1 else row)

        async def flush(self):
            return None

    db = FakeDB()
    monkeypatch.setattr(products_stocks, "require_bound_store", lambda user, sid: 7)
    monkeypatch.setattr(products_stocks, "assert_store_access", lambda user, sid: None)
    monkeypatch.setattr(products_stocks, "success", lambda data, request: data)
    monkeypatch.setattr(products_stocks, "enqueue_menu_cache_invalidation", AsyncMock())
    monkeypatch.setattr(products_stocks.AuditService, "log_staff", AsyncMock())
    monkeypatch.setattr("app.services.stock_service._emit_stock_mirror", AsyncMock())

    await products_stocks.upsert_store_stock(
        101, SimpleNamespace(store_id=7, stock=9, status=1),
        request=None, db=db, user=SimpleNamespace(),
    )

    assert len(db.statements) == 2
    assert "from products" in db.statements[0] and "for update" in db.statements[0]
    assert "store_product_stocks" in db.statements[1] and "for update" in db.statements[1]


@pytest.mark.asyncio
async def test_store_stock_delete_locks_product_before_store_override(monkeypatch):
    from app.api import products_stocks

    product = SimpleNamespace(id=101, store_id=7)
    row = SimpleNamespace(id=501)

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeDB:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement).lower())
            return Result(product if len(self.statements) == 1 else row)

        async def delete(self, obj):
            return None

        async def flush(self):
            return None

    db = FakeDB()
    monkeypatch.setattr(products_stocks, "require_bound_store", lambda user, sid: 7)
    monkeypatch.setattr(products_stocks, "assert_store_access", lambda user, sid: None)
    monkeypatch.setattr(products_stocks, "success", lambda data, request: data)
    monkeypatch.setattr(products_stocks, "enqueue_menu_cache_invalidation", AsyncMock())
    monkeypatch.setattr(products_stocks.AuditService, "log_staff", AsyncMock())
    monkeypatch.setattr("app.services.stock_service._emit_stock_mirror", AsyncMock())

    await products_stocks.delete_store_stock(
        101, request=None, db=db, user=SimpleNamespace(), store_id=7,
    )

    assert len(db.statements) == 2
    assert "from products" in db.statements[0] and "for update" in db.statements[0]
    assert "store_product_stocks" in db.statements[1] and "for update" in db.statements[1]

