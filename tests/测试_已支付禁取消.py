"""已付款订单禁止员工直接 PAID→CANCELLED。"""
import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_staff_transition


def test_staff_cannot_cancel_paid_order():
    with pytest.raises(AppException) as ei:
        assert_staff_transition("PAID", "CANCELLED")
    assert ei.value.code in (40020, 40022)
    assert "退款" in ei.value.message or "禁止" in ei.value.message or "非法" in ei.value.message


def test_staff_cannot_cancel_confirmed():
    with pytest.raises(AppException) as ei:
        assert_staff_transition("CONFIRMED", "CANCELLED")
    assert ei.value.code in (40020, 40022)


def test_staff_can_cancel_unpaid():
    assert_staff_transition("PENDING_PAYMENT", "CANCELLED")


def test_staff_cannot_set_cancelled_as_target_generic():
    """FORBIDDEN_STAFF_TARGETS 含 CANCELLED（未付款例外已覆盖）。"""
    with pytest.raises(AppException):
        assert_staff_transition("READY", "CANCELLED")
