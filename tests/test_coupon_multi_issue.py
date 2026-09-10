"""手动发券：同模板可批量多张（满减/折扣/代金券）。"""
from __future__ import annotations

import itertools
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import CouponTemplate, Customer, CustomerCoupon
from app.services.coupon_service import CouponService


def _customer(cid: int, no: str, nick: str) -> Customer:
    return Customer(
        id=cid,
        customer_no=no,
        nickname=nick,
        status=1,
        points=0,
    )


def _sqlite_auto_id(db_session, start: int = 8000):
    """SQLite 对 BigInteger PK 无自增，测试里补显式 id。"""
    ids = itertools.count(start)
    real_add = db_session.add

    def _add(obj):
        if isinstance(obj, CustomerCoupon) and getattr(obj, "id", None) is None:
            obj.id = next(ids)
        return real_add(obj)

    db_session.add = _add  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_issue_voucher_batch(db_session):
    cust = _customer(901, "CU_TEST_V901", "发券测试")
    tmpl = CouponTemplate(
        id=901,
        name="10元代金券",
        coupon_type="VOUCHER",
        discount_value=Decimal("10"),
        min_order_amount=Decimal("0"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([cust, tmpl])
    await db_session.flush()
    _sqlite_auto_id(db_session, 9100)

    nos: list[int] = []

    def _gen(prefix: str) -> str:
        nos.append(1)
        return f"{prefix}BATCH{len(nos):04d}"

    with patch("app.services.coupon_service.generate_no", side_effect=_gen):
        issued = await CouponService.issue_by_template_id(db_session, cust.id, tmpl.id, quantity=5)
        await db_session.flush()

    assert len(issued) == 5
    rows = (
        await db_session.execute(
            select(CustomerCoupon).where(
                CustomerCoupon.customer_id == cust.id,
                CustomerCoupon.template_id == tmpl.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_issue_fixed_coupon_batch(db_session):
    cust = _customer(902, "CU_TEST_F902", "满减批量")
    tmpl = CouponTemplate(
        id=902,
        name="满30减5",
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("30"),
        valid_days=30,
        status=1,
    )
    db_session.add_all([cust, tmpl])
    await db_session.flush()
    _sqlite_auto_id(db_session, 9200)

    nos: list[int] = []

    def _gen(prefix: str) -> str:
        nos.append(1)
        return f"{prefix}FIX{len(nos):04d}"

    with patch("app.services.coupon_service.generate_no", side_effect=_gen):
        issued = await CouponService.issue_by_template_id(db_session, cust.id, tmpl.id, quantity=3)
        await db_session.flush()

    assert len(issued) == 3
