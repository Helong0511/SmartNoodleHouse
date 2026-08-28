"""订单状态机与联动规则（无 DB）。"""
import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import (
    assert_payment_transition,
    assert_staff_transition,
    assert_system_transition,
)


def test_staff_kitchen_flow():
    assert_staff_transition("PAID", "PREPARING")
    assert_staff_transition("PREPARING", "COOKING")
    assert_staff_transition("COOKING", "READY")
    assert_staff_transition("READY", "COMPLETED")


def test_staff_cannot_set_paid_or_refunded():
    with pytest.raises(AppException) as e:
        assert_staff_transition("PENDING_PAYMENT", "PAID")
    assert e.value.code == 40020
    with pytest.raises(AppException):
        assert_staff_transition("PAID", "REFUNDED")


def test_payment_only_pending_to_paid():
    assert_payment_transition("PENDING_PAYMENT", "PAID")
    with pytest.raises(AppException):
        assert_payment_transition("PAID", "PAID")
    with pytest.raises(AppException):
        assert_payment_transition("PENDING_PAYMENT", "CONFIRMED")


def test_system_refund_from_kitchen_states():
    for cur in ("PAID", "CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED"):
        assert_system_transition(cur, "REFUNDED")


def test_system_timeout_cancel():
    assert_system_transition("PENDING_PAYMENT", "CANCELLED")


def test_system_delivery_rollback():
    assert_system_transition("CONFIRMED", "PAID")


def test_system_no_downgrade_ready_to_preparing():
    with pytest.raises(AppException) as e:
        assert_system_transition("READY", "PREPARING")
    assert e.value.code == 40025
