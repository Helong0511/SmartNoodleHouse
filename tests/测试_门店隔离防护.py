"""门店数据隔离纵深防御测试（P1/P2 加固项回归）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.exceptions import AppException


# ── P1-1: build_order_items 最终 store_id 断言 ──────────────────────────────


@pytest.mark.asyncio
async def test_build_order_items_rejects_mismatched_store(monkeypatch):
    """calculate_items_batch 已校验，但 build_order_items 仍需二次断言。"""
    from app.services.order_service import OrderPriceCalculator

    store_a, store_b = 1, 2
    product_b = SimpleNamespace(
        id=99, store_id=store_b, status="ON_SALE", name="B店面",
        price=10, box_price=0, no_member_discount=False,
    )

    # mock calculate_items_batch 直接返回跨店餐品行，单独测试 build_order_items 的断言
    async def fake_calc(db, items_data, *, store_id, customer_id=None):
        return [(product_b, 10, [], 1)]

    monkeypatch.setattr(OrderPriceCalculator, "calculate_items_batch", fake_calc)

    db = AsyncMock()
    order = SimpleNamespace(id=1, store_id=store_a)
    with pytest.raises(AppException) as ei:
        await OrderPriceCalculator.build_order_items(
            db, order, [{"product_id": 99, "quantity": 1, "option_ids": []}]
        )
    assert ei.value.code == 40002
    assert "门店" in ei.value.message


@pytest.mark.asyncio
async def test_build_order_items_accepts_matching_store(monkeypatch):
    """餐品门店与订单门店一致时正常通过。"""
    from app.services.order_service import OrderPriceCalculator

    store = 1
    product = SimpleNamespace(
        id=10, store_id=store, status="ON_SALE", name="同店面",
        price=12, box_price=0, no_member_discount=False,
    )

    async def fake_calc(db, items_data, *, store_id, customer_id=None):
        return [(product, 12, [], 1)]

    monkeypatch.setattr(OrderPriceCalculator, "calculate_items_batch", fake_calc)

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    order = SimpleNamespace(id=2, store_id=store)
    subtotal, packing, discountable = await OrderPriceCalculator.build_order_items(
        db, order, [{"product_id": 10, "quantity": 1, "option_ids": []}], deduct_stock=False
    )
    assert subtotal > 0


# ── P2-3: dining_session 归属校验 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_dining_session_owner_passes_when_no_session():
    """订单无 dining_session_id 时直接通过。"""
    from app.services.order_access import assert_dining_session_owner

    order = SimpleNamespace(id=1, dining_session_id=None)
    db = AsyncMock()
    await assert_dining_session_owner(db, order, 101)  # 不抛异常


@pytest.mark.asyncio
async def test_dining_session_owner_rejects_mismatch():
    """订单关联的 session 属于其他顾客时拒绝。"""
    from app.services.order_access import assert_dining_session_owner

    order = SimpleNamespace(id=1, dining_session_id=5)
    session = SimpleNamespace(id=5, customer_id=200)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)

    with pytest.raises(AppException) as ei:
        await assert_dining_session_owner(db, order, 101)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_dining_session_owner_accepts_match():
    """session 属于当前顾客时通过。"""
    from app.services.order_access import assert_dining_session_owner

    order = SimpleNamespace(id=1, dining_session_id=5)
    session = SimpleNamespace(id=5, customer_id=101)
    db = AsyncMock()
    db.get = AsyncMock(return_value=session)

    await assert_dining_session_owner(db, order, 101)  # 不抛异常


# ── P2-4: 摄像头流票据 store_id 绑定 ────────────────────────────────────────


def test_create_stream_ticket_includes_store_id():
    """创建票据时应编码 store_id。"""
    from app.core.security import create_stream_ticket, decode_token

    ticket = create_stream_ticket(user_id=1, camera_id=5, store_id=3)
    payload = decode_token(ticket)
    assert payload["typ"] == "stream"
    assert payload["cam"] == 5
    assert payload["store"] == 3


def test_create_stream_ticket_omits_store_id_when_none():
    """不传 store_id 时不编码（向后兼容）。"""
    from app.core.security import create_stream_ticket, decode_token

    ticket = create_stream_ticket(user_id=1, camera_id=5)
    payload = decode_token(ticket)
    assert "store" not in payload
