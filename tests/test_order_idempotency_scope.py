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


def test_idempotent_reuse_only_pending_payment_policy():
    """已付/已取消订单不得再被同幂等键复用（客户端须带新 checkout nonce）。"""
    pending = SimpleNamespace(status="PENDING_PAYMENT", payment_status="UNPAID")
    paid = SimpleNamespace(status="PAID", payment_status="PAID")
    cancelled = SimpleNamespace(status="CANCELLED", payment_status="UNPAID")

    assert OrderCreateService._is_reusable_idempotent_order(pending)
    assert not OrderCreateService._is_reusable_idempotent_order(paid)
    assert not OrderCreateService._is_reusable_idempotent_order(cancelled)
