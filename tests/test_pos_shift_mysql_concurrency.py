"""Real MySQL regression for POS shift close versus a delayed scan payment."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import CashierShift, Order, Payment, Role, Store, User
from app.services.pos_shift_service import PosShiftService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_shift_close_waits_for_payment_creation_then_blocks_pending_and_counts_late_callback(
    db_session,
):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    now = now_local()

    async with maker() as setup:
        store = Store(store_no=f"PS-{suffix[:20]}", name="POS shift fencing test")
        role = Role(code=f"PS_{suffix[:12]}", name="POS shift test role")
        setup.add_all([store, role])
        await setup.flush()
        user = User(
            username=f"ps_{suffix[:20]}",
            password_hash="not-a-real-password-hash",
            display_name="POS shift test cashier",
            role_id=role.id,
            store_id=store.id,
            status=1,
        )
        setup.add(user)
        await setup.flush()
        order = Order(
            order_no=f"PSO-{suffix[:28]}",
            store_id=store.id,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
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
        shift = CashierShift(
            store_id=store.id,
            operator_id=user.id,
            opening_float="100.00",
            status="OPEN",
            opened_at=now - timedelta(minutes=5),
        )
        setup.add(shift)
        await setup.flush()
        store_id, role_id, user_id = store.id, role.id, user.id
        order_id, shift_id = order.id, shift.id
        await setup.commit()

    second_close_lock = asyncio.Event()
    async with maker() as payment_session, maker() as close_session:
        locked_shift = await PosShiftService.require_open_shift_for_update(
            payment_session, user_id, store_id=store_id
        )
        assert locked_shift.id == shift_id
        payment = Payment(
            payment_no=f"PSP-{suffix[:28]}",
            order_id=order_id,
            operator_id=user_id,
            cashier_shift_id=locked_shift.id,
            provider="WECHAT",
            channel="POS",
            amount="10.00",
            status="PENDING",
        )
        payment_session.add(payment)
        await payment_session.flush()
        payment_id = payment.id
        payment_conn = (await payment_session.connection()).sync_connection
        stale_shift = await close_session.get(CashierShift, shift_id)
        assert stale_shift is not None

        def observe_close_lock(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is not payment_conn
                and "CASHIER_SHIFTS" in normalized
                and "FOR UPDATE" in normalized
            ):
                second_close_lock.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_close_lock)
        try:
            close_task = asyncio.create_task(
                PosShiftService.close_shift(
                    close_session,
                    stale_shift,
                    closing_cash=Decimal("100.00"),
                    remark=None,
                    closed_by=user_id,
                )
            )
            await asyncio.wait_for(second_close_lock.wait(), timeout=10)
            # Simulate the scan-payment request persisting its PENDING row while
            # holding the same shift lock. Closing may proceed only after commit,
            # then must see the newly committed pending payment.
            await payment_session.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(close_task, timeout=15)
            assert exc.value.code == 40909
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_close_lock)
            if payment_session.in_transaction():
                await payment_session.rollback()
            if close_session.in_transaction():
                await close_session.rollback()

    # The channel callback arrives later and records success after the time at
    # which the cashier first attempted to close. Shift ID, not paid_at, owns it.
    async with maker() as callback_session:
        payment = await callback_session.get(Payment, payment_id)
        assert payment is not None
        payment.status = "PAID"
        payment.provider_trade_no = f"WX-TX-{suffix}"
        payment.paid_at = now_local() + timedelta(minutes=10)
        await callback_session.commit()

    async with maker() as closing:
        shift = await closing.get(CashierShift, shift_id)
        assert shift is not None
        closed = await PosShiftService.close_shift(
            closing,
            shift,
            closing_cash=Decimal("100.00"),
            remark="delayed payment callback regression",
            closed_by=user_id,
        )
        await closing.commit()
        assert closed.status == "CLOSED"
        assert Decimal(str(closed.total_wechat)) == Decimal("10.00")
        assert Decimal(str(closed.total_amount)) == Decimal("10.00")

    async with maker() as cleanup:
        await cleanup.execute(delete(Payment).where(Payment.id == payment_id))
        await cleanup.execute(delete(Order).where(Order.id == order_id))
        await cleanup.execute(delete(CashierShift).where(CashierShift.id == shift_id))
        await cleanup.execute(delete(User).where(User.id == user_id))
        await cleanup.execute(delete(Role).where(Role.id == role_id))
        await cleanup.execute(delete(Store).where(Store.id == store_id))
        await cleanup.commit()
