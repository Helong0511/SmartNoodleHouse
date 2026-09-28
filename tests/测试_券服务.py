"""优惠券计价逻辑测试。"""
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException
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


def test_percent_beats_member_same_rate_disabled():
    assert CouponService.percent_beats_member(Decimal("0.90"), Decimal("0.90")) is False


def test_percent_beats_member_weaker_disabled():
    assert CouponService.percent_beats_member(Decimal("0.95"), Decimal("0.90")) is False


def test_percent_beats_member_stronger_ok():
    assert CouponService.percent_beats_member(Decimal("0.80"), Decimal("0.95")) is True


def test_effective_member_rate_skips_when_percent_better():
    tmpl = SimpleNamespace(coupon_type="PERCENT", discount_value=Decimal("0.80"))
    assert CouponService.effective_member_rate_with_coupon(Decimal("0.95"), tmpl) == Decimal("1")


def test_effective_member_rate_keeps_when_percent_same():
    tmpl = SimpleNamespace(coupon_type="PERCENT", discount_value=Decimal("0.90"))
    assert CouponService.effective_member_rate_with_coupon(Decimal("0.90"), tmpl) == Decimal("0.90")


def test_effective_member_rate_keeps_for_fixed():
    tmpl = SimpleNamespace(coupon_type="FIXED", discount_value=Decimal("5"))
    assert CouponService.effective_member_rate_with_coupon(Decimal("0.90"), tmpl) == Decimal("0.90")


def test_hold_payload_includes_template_name():
    cc = SimpleNamespace(id=1, coupon_no="C001", expires_at=None)
    tmpl = SimpleNamespace(
        name="新客满30减5",
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("30"),
    )
    payload = CouponService._hold_payload(cc, tmpl)
    assert payload["name"] == "新客满30减5"
    assert payload["template_name"] == "新客满30减5"
    assert payload["summary"] == CouponService.summary_label(tmpl)


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


@pytest.mark.asyncio
async def test_resolve_percent_rejects_same_as_member():
    tmpl = SimpleNamespace(
        coupon_type="PERCENT",
        discount_value=Decimal("0.90"),
        min_order_amount=Decimal("0"),
        store_id=None,
        store_whitelist=None,
    )
    cc = SimpleNamespace(
        id=10,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
        issued_at_store_id=None,
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    db.flush = AsyncMock()
    with pytest.raises(AppException) as ei:
        await CouponService.resolve_discount(
            db, 10, 1, Decimal("100"), member_rate=Decimal("0.90")
        )
    assert ei.value.code == 40074


@pytest.mark.asyncio
async def test_resolve_percent_applies_when_better_than_member():
    tmpl = SimpleNamespace(
        coupon_type="PERCENT",
        discount_value=Decimal("0.80"),
        min_order_amount=Decimal("0"),
        store_id=None,
        store_whitelist=None,
    )
    cc = SimpleNamespace(
        id=11,
        customer_id=1,
        status="AVAILABLE",
        expires_at=datetime.now() + timedelta(days=7),
        issued_at_store_id=None,
    )
    row = MagicMock()
    row.first.return_value = (cc, tmpl)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    db.flush = AsyncMock()
    disc, cid = await CouponService.resolve_discount(
        db, 11, 1, Decimal("100"), member_rate=Decimal("0.95")
    )
    assert cid == 11
    assert disc == Decimal("20.00")
