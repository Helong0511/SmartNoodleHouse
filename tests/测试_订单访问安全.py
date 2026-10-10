"""顾客订单访问控制：未绑定 POS 不可 claim；跨顾客拒绝。"""
from types import SimpleNamespace

import pytest

from app.core.exceptions import AppException
from app.services.order_access import assert_customer_can_pay_order, assert_customer_owns_order


def _order(**kw):
    defaults = {"id": 1, "customer_id": None, "channel": "POS", "store_id": 1}
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_customer_cannot_access_unbound_pos_order():
    with pytest.raises(AppException) as ei:
        assert_customer_owns_order(_order(customer_id=None, channel="POS"), 101)
    assert ei.value.status_code == 403
    assert "未绑定" in ei.value.message


def test_customer_cannot_pay_unbound_pos_order():
    with pytest.raises(AppException) as ei:
        assert_customer_can_pay_order(_order(customer_id=None, channel="POS"), 101)
    assert ei.value.status_code == 403


def test_customer_a_cannot_access_customer_b_order():
    with pytest.raises(AppException) as ei:
        assert_customer_owns_order(_order(customer_id=200, channel="WECHAT"), 101)
    assert ei.value.status_code == 403
    assert "无权访问" in ei.value.message


def test_owner_can_access_own_order():
    assert_customer_owns_order(_order(customer_id=101, channel="WECHAT"), 101)
    assert_customer_can_pay_order(_order(customer_id=101, channel="ALIPAY"), 101)


def test_unbound_non_pos_also_denied():
    """无 customer_id 的非 POS 单同样拒绝顾客访问（防 claim）。"""
    with pytest.raises(AppException) as ei:
        assert_customer_owns_order(_order(customer_id=None, channel="WECHAT"), 101, action="同步 POS")
    assert ei.value.status_code == 403
