"""优惠券预占/释放逻辑测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException
from app.core.response import now_local
from app.services.coupon_service import CouponService


@pytest.mark.asyncio
async def test_reserve_for_order_marks_reserved():
    cc = SimpleNamespace(
        id=10,
        customer_id=1,
        status="AVAILABLE",
        used_order_id=None,
    )
    row = MagicMock()
    row.scalar_one_or_none.return_value = cc
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)
    db.flush = AsyncMock()

    await CouponService.reserve_for_order(db, 10, order_id=99, customer_id=1)

    assert cc.status == "RESERVED"
    assert cc.used_order_id == 99


@pytest.mark.asyncio
async def test_reserve_for_order_rejects_unavailable():
    row = MagicMock()
    row.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=row)

    with pytest.raises(AppException) as ei:
        await CouponService.reserve_for_order(db, 10, order_id=99, customer_id=1)
    assert ei.value.code == 40063


@pytest.mark.asyncio
async def test_release_for_order_restores_reserved():
    cc = SimpleNamespace(
        id=10,
        status="RESERVED",
        used_order_id=99,
        used_at=now_local(),
    )
    order = SimpleNamespace(id=99, customer_coupon_id=10, customer_voucher_id=None)
    db = AsyncMock()
    db.get = AsyncMock(return_value=cc)
    db.flush = AsyncMock()

    await CouponService.release_for_order(db, order)

    assert cc.status == "AVAILABLE"
    assert cc.used_order_id is None
    assert cc.used_at is None
