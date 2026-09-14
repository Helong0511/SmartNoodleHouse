"""厨房 KDS 超时自动出餐规则（无 DB）。"""
from datetime import datetime, timedelta, timezone

from app.services.kitchen_auto_service import (
    EXCLUDED_CHANNELS,
    KITCHEN_PIPELINE_STATUSES,
    order_kitchen_anchor_at,
)
from app.services.order_state_machine import assert_system_transition


def test_kitchen_pipeline_statuses_match_kds_active():
    kds_active = {"PAID", "CONFIRMED", "PREPARING", "COOKING", "READY"}
    assert set(KITCHEN_PIPELINE_STATUSES) == kds_active


def test_system_can_auto_complete_from_kitchen_states():
    for cur in KITCHEN_PIPELINE_STATUSES:
        assert_system_transition(cur, "COMPLETED")


def test_excluded_delivery_channels():
    assert "MEITUAN" in EXCLUDED_CHANNELS
    assert "ELEME" in EXCLUDED_CHANNELS


def test_order_kitchen_anchor_prefers_paid_at():
    paid = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    created = datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc)

    class O:
        paid_at = paid
        ordered_at = None
        created_at = created

    assert order_kitchen_anchor_at(O()) == paid


def test_cutoff_eligibility_by_anchor():
    now = datetime(2026, 8, 31, 13, 0, tzinfo=timezone.utc)
    timeout = 20
    cutoff = now - timedelta(minutes=timeout)

    class O:
        paid_at = datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc)
        ordered_at = None
        created_at = None

    assert order_kitchen_anchor_at(O()) <= cutoff

    class O2:
        paid_at = datetime(2026, 8, 31, 12, 50, tzinfo=timezone.utc)
        ordered_at = None
        created_at = None

    assert order_kitchen_anchor_at(O2()) > cutoff
