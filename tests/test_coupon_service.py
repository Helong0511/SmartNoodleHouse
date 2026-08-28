"""优惠券计价逻辑测试。"""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.response import ensure_local_aware, now_local
from app.services.coupon_service import CouponService


def test_discount_label_fixed():
    tmpl = SimpleNamespace(coupon_type="FIXED", discount_value=Decimal("5"), min_order_amount=Decimal("30"))
    assert CouponService.summary_label(tmpl) == "满30.00减¥5.00"


def test_discount_label_percent():
    tmpl = SimpleNamespace(coupon_type="PERCENT", discount_value=Decimal("0.9"), min_order_amount=Decimal("50"))
    assert "9折" in CouponService.discount_label(tmpl)


def test_discount_label_voucher():
    tmpl = SimpleNamespace(coupon_type="VOUCHER", discount_value=Decimal("10"), min_order_amount=Decimal("0"))
    assert "10" in CouponService.discount_label(tmpl)
    assert "代金券" in CouponService.discount_label(tmpl)


def test_ensure_local_aware_naive():
    naive = datetime(2026, 8, 28, 12, 0, 0)
    aware = ensure_local_aware(naive)
    assert aware is not None
    assert aware.tzinfo is not None
    assert aware <= now_local() or aware > now_local()


@pytest.mark.asyncio
async def test_resolve_voucher_skips_when_payable_less_than_face():
    """应付低于代金券面额时不使用，避免一次性核销浪费余额。"""
    tmpl = SimpleNamespace(
        coupon_type="VOUCHER",
        discount_value=Decimal("10"),
        min_order_amount=Decimal("0"),
    )
    cc = SimpleNamespace(
        id=2,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    disc, vid = await CouponService.resolve_voucher_discount(db, 2, 1, Decimal("8"))
    assert disc == Decimal("0.00")
    assert vid is None


@pytest.mark.asyncio
async def test_resolve_voucher_applies_when_payable_meets_face():
    tmpl = SimpleNamespace(
        coupon_type="VOUCHER",
        discount_value=Decimal("10"),
        min_order_amount=Decimal("0"),
    )
    cc = SimpleNamespace(
        id=3,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    disc, vid = await CouponService.resolve_voucher_discount(db, 3, 1, Decimal("12"))
    assert disc == Decimal("10.00")
    assert vid == 3


@pytest.mark.asyncio
async def test_resolve_voucher_skips_when_eligible_less_than_face():
    """可参与代金券的基数（不含特价）不足面额时不使用。"""
    tmpl = SimpleNamespace(
        coupon_type="VOUCHER",
        discount_value=Decimal("10"),
        min_order_amount=Decimal("0"),
    )
    cc = SimpleNamespace(
        id=4,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    disc, vid = await CouponService.resolve_voucher_discount(
        db, 4, 1, Decimal("48.00"), eligible_payable=Decimal("8.00")
    )
    assert disc == Decimal("0.00")
    assert vid is None


@pytest.mark.asyncio
async def test_resolve_voucher_uses_eligible_cap():
    tmpl = SimpleNamespace(
        coupon_type="VOUCHER",
        discount_value=Decimal("10"),
        min_order_amount=Decimal("0"),
    )
    cc = SimpleNamespace(
        id=5,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    disc, vid = await CouponService.resolve_voucher_discount(
        db, 5, 1, Decimal("95.80"), eligible_payable=Decimal("60.80")
    )
    assert disc == Decimal("10.00")
    assert vid == 5


@pytest.mark.asyncio
async def test_resolve_discount_naive_expires_at():
    """DB 读出 naive expires_at 时不应因时区比较抛 TypeError。"""
    tmpl = SimpleNamespace(
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("30"),
    )
    cc = SimpleNamespace(
        id=1,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    db.flush = AsyncMock()
    disc, cid = await CouponService.resolve_discount(db, 1, 1, Decimal("100"))
    assert disc == Decimal("5.00")
    assert cid == 1
