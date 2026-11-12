"""Real MySQL concurrency regression for order terminal transitions."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Order, Store
from app.services import order_lifecycle
from app.services.coupon_service import CouponService
from app.services.social_benefit_service import SocialBenefitService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_terminal_transition_uses_locked_current_state_and_runs_side_effects_once(
    db_session, monkeypatch
):
    """A stale RR snapshot must not permit a second terminal transition.

    The second session loads PAID before the first session commits REFUNDED.
    Its later SELECT ... FOR UPDATE must be a MySQL current read, not the stale
    repeatable-read snapshot. The stale caller object must also be refreshed.
    """
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    maker = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
        autoflush=False,
    )
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"CC-{suffix[:20]}", name="Concurrency test")
        setup.add(store)
        await setup.flush()
        order = Order(
            order_no=f"OC-{suffix[:30]}",
            store_id=store.id,
            status="PAID",
            payment_status="PAID",
            subtotal="10.00",
            total_amount="10.00",
            discount_amount="0.00",
            coupon_discount="0.00",
            voucher_discount="0.00",
            packing_fee="0.00",
            dining_type="TAKEOUT",
        )
        setup.add(order)
        await setup.flush()
        order_id = order.id
        await setup.commit()

    restore_stock = AsyncMock()
    release_coupon = AsyncMock()
    reverse_social = AsyncMock()
    record_status = AsyncMock()
    monkeypatch.setattr(order_lifecycle, "_restore_order_stock", restore_stock)
    monkeypatch.setattr(order_lifecycle, "record_order_status", record_status)
    monkeypatch.setattr(CouponService, "release_for_order", release_coupon)
    monkeypatch.setattr(SocialBenefitService, "reverse_for_order", reverse_social)

    first_locked = asyncio.Event()
    second_started = asyncio.Event()

    async with maker() as first, maker() as second:
        first_order = await first.get(Order, order_id)
        stale_second_order = await second.get(Order, order_id)
        assert first_order is not None and stale_second_order is not None
        assert first_order.status == stale_second_order.status == "PAID"

        await order_lifecycle.apply_order_status(
            first,
            first_order,
            "REFUNDED",
            source="PAYMENT",
            assert_kind="SYSTEM",
        )
        first_locked.set()

        async def competing_transition():
            await first_locked.wait()
            second_started.set()
            try:
                await order_lifecycle.apply_order_status(
                    second,
                    stale_second_order,
                    "CANCELLED",
                    source="SYSTEM",
                    assert_kind="SYSTEM",
                )
            except AppException as exc:
                return exc.code
            return None

        competing = asyncio.create_task(competing_transition())
        await second_started.wait()
        # Commit the first transition only after the competing task has started.
        await first.commit()
        error_code = await asyncio.wait_for(competing, timeout=15)
        await second.rollback()

        assert error_code == 40025
        assert stale_second_order.status == "REFUNDED"

    async with maker() as verify:
        persisted = await verify.get(Order, order_id)
        assert persisted is not None
        assert persisted.status == "REFUNDED"

    # Only the winning PAID -> REFUNDED transition may execute terminal side effects.
    restore_stock.assert_awaited_once()
    release_coupon.assert_awaited_once()
    reverse_social.assert_awaited_once()
    record_status.assert_awaited_once()
