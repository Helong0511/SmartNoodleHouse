"""Real MySQL concurrency regression for order terminal transitions."""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Order, OutboxEvent, Payment, PaymentStatusHistory, Store
from app.services import order_lifecycle
from app.services.financial_ledger_service import FinancialLedgerService
from app.services.payment_service import PaymentService
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
    repeatable-read snapshot. A SQLAlchemy cursor barrier proves the competing
    locking SELECT has reached the DB before the winner commits.
    """
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(
        engine,
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

    second_lock_query = asyncio.Event()

    def observe_competing_lock(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "FOR UPDATE" in normalized and "ORDERS.STATUS" in normalized:
            second_lock_query.set()

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

        event.listen(engine.sync_engine, "before_cursor_execute", observe_competing_lock)
        try:
            competing = asyncio.create_task(
                order_lifecycle.apply_order_status(
                    second,
                    stale_second_order,
                    "CANCELLED",
                    source="SYSTEM",
                    assert_kind="SYSTEM",
                )
            )
            # This event fires inside the second connection immediately before
            # MySQL executes SELECT ... FOR UPDATE; the first transaction still
            # owns the row lock at this point.
            await asyncio.wait_for(second_lock_query.wait(), timeout=10)
            await first.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(competing, timeout=15)
            assert exc.value.code == 40025
            # Verify the ORM object was synchronized before rollback expires it.
            assert stale_second_order.status == "REFUNDED"
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_competing_lock)
            await second.rollback()

    async with maker() as verify:
        persisted = await verify.get(Order, order_id)
        assert persisted is not None
        assert persisted.status == "REFUNDED"
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Store).where(Store.id == persisted.store_id))
        await verify.commit()

    # Only the winning PAID -> REFUNDED transition may execute terminal side effects.
    restore_stock.assert_awaited_once()
    release_coupon.assert_awaited_once()
    reverse_social.assert_awaited_once()
    record_status.assert_awaited_once()



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_replayed_terminal_callback_does_not_repeat_side_effects(db_session, monkeypatch):
    """A callback replay after commit must not repeat terminal side effects."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    # Match production AsyncSessionLocal (autoflush=True). _finalize_tender updates Payment.status before paid_amount SELECT; disabling autoflush makes the aggregate query miss this transaction's own payment.
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"CR-{suffix[:20]}", name="Callback replay test")
        setup.add(store)
        await setup.flush()
        order = Order(
            order_no=f"OR-{suffix[:30]}",
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

    async with maker() as first:
        first_order = await first.get(Order, order_id)
        assert first_order is not None
        await order_lifecycle.apply_order_status(
            first,
            first_order,
            "REFUNDED",
            source="PAYMENT",
            assert_kind="SYSTEM",
        )
        await first.commit()

    # Simulate a provider callback replay in a new transaction after the first
    # terminal transition is durably committed.
    async with maker() as replay:
        replay_order = await replay.get(Order, order_id)
        assert replay_order is not None
        assert replay_order.status == "REFUNDED"
        await order_lifecycle.apply_order_status(
            replay,
            replay_order,
            "REFUNDED",
            source="PAYMENT",
            assert_kind="SYSTEM",
        )
        await replay.commit()

    async with maker() as verify:
        persisted = await verify.get(Order, order_id)
        assert persisted is not None
        assert persisted.status == "REFUNDED"
        store_id = persisted.store_id
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()

    restore_stock.assert_awaited_once()
    release_coupon.assert_awaited_once()
    reverse_social.assert_awaited_once()
    record_status.assert_awaited_once()



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_payment_callback_waiting_on_cancel_queues_one_durable_auto_refund(
    db_session, monkeypatch
):
    """A late successful callback must not revive a cancelled order.

    The cancellation transaction owns the Order row lock while the independent
    callback session reaches its Order -> Payment locking read. Committing the
    cancellation releases the lock; the callback must then observe CANCELLED,
    persist the captured payment, and enqueue exactly one auto-refund event.
    """
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    # Match production AsyncSessionLocal autoflush: flush Payment.status=PAID before the locked paid_amount query.
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"LR-{suffix[:20]}", name="Late payment race test")
        setup.add(store)
        await setup.flush()
        order = Order(
            order_no=f"LR-{suffix[:30]}",
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
        payment = Payment(
            payment_no=f"LP-{suffix[:30]}",
            order_id=order.id,
            provider="ALIPAY",
            channel="ALIPAY_QR",
            amount="10.00",
            currency="CNY",
            status="PENDING",
        )
        setup.add(payment)
        await setup.flush()
        order_id, payment_id, store_id = order.id, payment.id, store.id
        payment_no = payment.payment_no
        await setup.commit()

    restore_stock = AsyncMock()
    release_coupon = AsyncMock()
    reverse_social = AsyncMock()
    record_order_status = AsyncMock()
    record_sale = AsyncMock()
    monkeypatch.setattr(order_lifecycle, "_restore_order_stock", restore_stock)
    monkeypatch.setattr(order_lifecycle, "record_order_status", record_order_status)
    monkeypatch.setattr(CouponService, "release_for_order", release_coupon)
    monkeypatch.setattr(SocialBenefitService, "reverse_for_order", reverse_social)
    monkeypatch.setattr(FinancialLedgerService, "record_sale", record_sale)

    callback_order_lock = asyncio.Event()

    def observe_callback_lock(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "FOR UPDATE" in normalized and "ORDERS.ORDER_NO" in normalized:
            callback_order_lock.set()

    class AlipayCallback:
        async def handle_callback(self, _data):
            return {
                "payment_no": payment_no,
                "provider_trade_no": f"ALI-TX-{suffix}",
                "amount": "10.00",
                "currency": "CNY",
                "status": "PAID",
                "app_id": None,
            }

    callback_data = {
        "out_trade_no": payment_no,
        "trade_no": f"ALI-TX-{suffix}",
        "total_amount": "10.00",
        "trade_status": "TRADE_SUCCESS",
    }

    async with maker() as cancel_db, maker() as callback_db:
        cancel_order = await cancel_db.get(Order, order_id)
        assert cancel_order is not None
        await order_lifecycle.apply_order_status(
            cancel_db,
            cancel_order,
            "CANCELLED",
            source="SYSTEM",
            assert_kind="SYSTEM",
        )

        event.listen(engine.sync_engine, "before_cursor_execute", observe_callback_lock)
        try:
            callback_task = asyncio.create_task(
                PaymentService._apply_payment_callback(
                    callback_db,
                    "ALIPAY",
                    AlipayCallback(),
                    callback_data,
                    receipt=f"late-payment-{suffix}",
                )
            )
            # The callback has reached its Order FOR UPDATE statement while
            # cancellation still owns the row lock; no timing sleep is used.
            await asyncio.wait_for(callback_order_lock.wait(), timeout=10)
            await cancel_db.commit()
            callback_payment = await asyncio.wait_for(callback_task, timeout=20)
            assert callback_payment is not None
            await callback_db.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_callback_lock)
            if callback_db.in_transaction():
                await callback_db.rollback()
            if cancel_db.in_transaction():
                await cancel_db.rollback()

    async with maker() as verify:
        persisted_order = await verify.get(Order, order_id)
        persisted_payment = await verify.get(Payment, payment_id)
        assert persisted_order is not None and persisted_payment is not None
        assert persisted_order.status == "CANCELLED"
        assert persisted_order.payment_status == "PAID"
        assert persisted_payment.status == "PAID"
        outbox_rows = (
            await verify.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "late_payment",
                    OutboxEvent.aggregate_id == payment_id,
                    OutboxEvent.event_kind == "auto_refund",
                )
            )
        ).scalars().all()
        assert len(outbox_rows) == 1
        assert outbox_rows[0].status == "PENDING"
        assert json.loads(outbox_rows[0].payload or "{}").get("payment_id") == payment_id
        assert await verify.scalar(
            select(func.count()).select_from(OutboxEvent).where(
                OutboxEvent.aggregate_type == "late_payment",
                OutboxEvent.aggregate_id == payment_id,
                OutboxEvent.event_kind == "auto_refund",
            )
        ) == 1

        await verify.execute(
            delete(OutboxEvent).where(
                OutboxEvent.aggregate_type == "late_payment",
                OutboxEvent.aggregate_id == payment_id,
            )
        )
        await verify.execute(
            delete(PaymentStatusHistory).where(PaymentStatusHistory.payment_id == payment_id)
        )
        await verify.execute(delete(Payment).where(Payment.id == payment_id))
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()

    restore_stock.assert_awaited_once()
    release_coupon.assert_awaited_once()
    reverse_social.assert_awaited_once()
    record_order_status.assert_awaited_once()
    record_sale.assert_awaited_once()
