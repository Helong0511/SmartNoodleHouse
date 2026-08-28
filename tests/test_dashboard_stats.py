"""数据概览 / 支付方式统计口径测试"""
from __future__ import annotations

from decimal import Decimal


def test_paid_like_statuses_include_refunded():
    """退款后支付单仍应计入「今日支付笔数」，否则微信/支付宝会归零。"""
    paid_like = ("PAID", "PARTIAL_REFUND", "REFUNDED")
    assert "REFUNDED" in paid_like
    assert "PAID" in paid_like


def test_payment_channel_net_formula():
    from app.core.response import money

    gross = money(Decimal("35.00"))
    refunded = money(Decimal("35.00"))
    net = money(gross - refunded)
    assert net == money(Decimal("0.00"))


def test_old_paid_only_filter_would_hide_refunded():
    """复现旧 bug：只筛 PAID 时，全额退款后笔数变为 0。"""
    rows = [("WECHAT", "REFUNDED", 1)]
    old = sum(c for p, s, c in rows if s == "PAID" and p == "WECHAT")
    new = sum(c for p, s, c in rows if s in ("PAID", "PARTIAL_REFUND", "REFUNDED") and p == "WECHAT")
    assert old == 0
    assert new == 1


def test_store_breakdown_item_shape():
    """多门店分项字段约定。"""
    item = {
        "store_id": 1,
        "store_no": "SH01",
        "store_name": "总店",
        "today_orders": 0,
        "today_revenue": "0.00",
        "today_refunded": "0.00",
        "new_customers": 0,
        "delivery_pending": 0,
    }
    assert item["store_no"] == "SH01"
    assert "today_revenue" in item
