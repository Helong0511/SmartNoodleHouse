"""幂等键作用域：不同顾客同 key 不撞单；命中后归属校验。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.services.customer_service import OrderCreateService


def test_different_customers_same_raw_key_do_not_collide():
    a = OrderCreateService._scoped_idempotency_key(
        "same-key", channel="WECHAT", customer_id=1, store_id=10, operator_id=None
    )
    b = OrderCreateService._scoped_idempotency_key(
        "same-key", channel="WECHAT", customer_id=2, store_id=10, operator_id=None
    )
    assert a != b
    assert a.startswith("cust:1:")
    assert b.startswith("cust:2:")


def test_pos_key_scoped_by_store_and_operator():
    k = OrderCreateService._scoped_idempotency_key(
        "k1", channel="POS", customer_id=None, store_id=3, operator_id=9
    )
    assert k == "pos:3:9:k1" or k.startswith("pos:3:9:")


def test_idempotent_hit_rejects_other_customer():
    order = SimpleNamespace(id=1, customer_id=100, store_id=1, channel="WECHAT", operator_id=None)
    with pytest.raises(AppException) as ei:
        OrderCreateService._assert_idempotent_order_match(
            order,
            customer_id=200,
            store_id=1,
            channel="WECHAT",
            operator_id=None,
        )
    assert ei.value.code == 40902
    assert "顾客" in ei.value.message


def test_idempotent_hit_rejects_channel_mismatch():
    order = SimpleNamespace(id=1, customer_id=100, store_id=1, channel="WECHAT", operator_id=None)
    with pytest.raises(AppException) as ei:
        OrderCreateService._assert_idempotent_order_match(
            order,
            customer_id=100,
            store_id=1,
            channel="ALIPAY",
            operator_id=None,
        )
    assert ei.value.code == 40902


def test_idempotent_hit_same_owner_ok():
    order = SimpleNamespace(id=1, customer_id=100, store_id=1, channel="WECHAT", operator_id=None)
    OrderCreateService._assert_idempotent_order_match(
        order,
        customer_id=100,
        store_id=1,
        channel="WECHAT",
        operator_id=None,
    )


@pytest.mark.asyncio
async def test_idempotent_replay_rejects_changed_order_items():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    existing = SimpleNamespace(id=10, product_id=1, quantity=2, remark="少辣")
    rows = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [existing])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=rows))
    order = SimpleNamespace(id=9)

    with pytest.raises(AppException) as exc:
        await OrderCreateService._assert_idempotent_order_items_match(
            db,
            order,
            [{"product_id": 2, "quantity": 2, "remark": "少辣", "option_ids": []}],
        )
    assert exc.value.code == 40902
    assert "餐品、数量或备注" in exc.value.message


@pytest.mark.asyncio
async def test_idempotent_replay_accepts_same_order_items():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    existing = SimpleNamespace(id=10, product_id=1, quantity=2, remark="少辣")
    rows = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [existing])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=rows))
    await OrderCreateService._assert_idempotent_order_items_match(
        db,
        SimpleNamespace(id=9),
        [{"product_id": 1, "quantity": 2, "remark": "少辣", "option_ids": []}],
    )


def test_idempotent_reuse_only_pending_payment_policy():
    """已付/已取消订单不得再被同幂等键复用（客户端须带新 checkout nonce）。"""
    pending = SimpleNamespace(status="PENDING_PAYMENT", payment_status="UNPAID")
    paid = SimpleNamespace(status="PAID", payment_status="PAID")
    cancelled = SimpleNamespace(status="CANCELLED", payment_status="UNPAID")

    assert OrderCreateService._is_reusable_idempotent_order(pending)
    assert not OrderCreateService._is_reusable_idempotent_order(paid)
    assert not OrderCreateService._is_reusable_idempotent_order(cancelled)


@pytest.mark.asyncio
async def test_create_order_rejects_idempotent_hit_on_paid_order():
    """命中已付订单的幂等键应抛 40903，而非静默返回旧单。"""
    from unittest.mock import AsyncMock, MagicMock, patch

    paid = SimpleNamespace(
        id=9,
        status="PAID",
        payment_status="PAID",
        customer_id=1,
        store_id=1,
        channel="H5",
        operator_id=None,
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(status=1))
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=paid)))

    with patch.object(OrderCreateService, "_scoped_idempotency_key", return_value="cust:1:k"):
        with pytest.raises(AppException) as ei:
            await OrderCreateService.create_order(
                db,
                store_id=1,
                table_token=None,
                customer_id=1,
                items=[{"product_id": 1, "quantity": 1}],
                channel="H5",
                idempotency_key="k",
            )
    assert ei.value.code == 40903
    assert "重新下单" in ei.value.message


@pytest.mark.asyncio
async def test_http_create_order_returns_40903_envelope():
    """网关应透传 AppException.code=40903，供客户端清 nonce。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_current_customer, get_db
    from app.core.exceptions import AppException
    from app.core.security import create_customer_token
    from app.main import app

    fake_customer = SimpleNamespace(id=1, status=1, token_version=0)
    fake_db = AsyncMock()

    async def override_db():
        yield fake_db

    async def override_customer():
        return fake_customer

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_customer] = override_customer
    token = create_customer_token(1, "wechat")
    try:
        with patch(
            "app.api.orders.OrderCreateService.create_order",
            AsyncMock(side_effect=AppException(40903, "该幂等键对应订单已结束，请重新下单")),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/api/orders",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "store_id": 1,
                        "channel": "H5",
                        "idempotency_key": "reuse-paid-key",
                        "items": [{"product_id": 1, "quantity": 1}],
                    },
                )
        assert res.status_code == 400
        body = res.json()
        assert body["code"] == 40903
        assert "重新下单" in (body.get("message") or "")
        assert body.get("data") is None
    finally:
        app.dependency_overrides.clear()


def test_long_idempotency_keys_hash_full_scope_without_truncation():
    """超长幂等键把完整 scope 纳入 256-bit 摘要，不截断摘要位数。"""
    raw = "k" * 512
    values = [
        OrderCreateService._scoped_idempotency_key(
            raw, channel="WECHAT", customer_id=1, store_id=10, operator_id=None
        ),
        OrderCreateService._scoped_idempotency_key(
            raw, channel="WECHAT", customer_id=2, store_id=10, operator_id=None
        ),
        OrderCreateService._scoped_idempotency_key(
            raw, channel="POS", customer_id=None, store_id=3, operator_id=9
        ),
        OrderCreateService._scoped_idempotency_key(
            raw, channel="POS", customer_id=None, store_id=3, operator_id=10
        ),
    ]
    assert all(len(value) == 64 for value in values)
    assert len(set(values)) == len(values)
