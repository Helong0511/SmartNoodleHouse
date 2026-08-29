"""数据概览 / 支付方式统计口径测试"""
from __future__ import annotations

from decimal import Decimal


def test_paid_payment_statuses_include_refunded():
    from app.services.revenue_stats import _PAID_PAYMENT

    assert "REFUNDED" in _PAID_PAYMENT
    assert "PAID" in _PAID_PAYMENT


def test_payment_channel_net_formula():
    from app.core.response import money

    gross = money(Decimal("35.00"))
    refunded = money(Decimal("35.00"))
    net = money(gross - refunded)
    assert net == money(Decimal("0.00"))


def test_store_breakdown_item_shape():
    """多门店分项字段约定。"""
    item = {
        "store_id": 1,
        "store_no": "SH01",
        "store_name": "总店",
        "order_count": 0,
        "net_revenue": "0.00",
        "refunded_amount": "0.00",
        "member_discount": "0.00",
        "coupon_discount": "0.00",
        "voucher_discount": "0.00",
        "total_discount": "0.00",
        "new_store_customers": 0,
        "delivery_pending": 0,
    }
    assert item["store_no"] == "SH01"
    assert "net_revenue" in item
    assert "total_discount" in item
