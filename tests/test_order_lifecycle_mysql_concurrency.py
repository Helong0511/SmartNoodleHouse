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
from app.models import (Order, OrderItem, OrderStatusHistory, OutboxEvent, Payment, PaymentStatusHistory, Product, ProductCategory, Store, StoreProductStock)
from app.services import order_lifecycle, stock_service
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


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_overlapping_order_stock_restoration_uses_sorted_product_locks(
    db_session, monkeypatch
):
    """Two orders with reversed item insertion order restore shared stock safely."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"SL-{suffix[:20]}", name="Stock lock ordering test")
        setup.add(store)
        await setup.flush()
        category = ProductCategory(
            store_id=store.id,
            category_no=f"SL-{suffix[:16]}",
            name="Stock lock test",
        )
        setup.add(category)
        await setup.flush()
        products = [
            Product(
                store_id=store.id,
                category_id=category.id,
                product_no=f"SL-{suffix[:12]}-{index}",
                name=f"Stock lock product {index}",
                price="5.00",
                stock=20,
                sales_count=2,
            )
            for index in (1, 2)
        ]
        setup.add_all(products)
        await setup.flush()
        products.sort(key=lambda p: p.id)
        stock_rows = [
            StoreProductStock(store_id=store.id, product_id=p.id, stock=5, status=1)
            for p in products
        ]
        setup.add_all(stock_rows)
        orders = [
            Order(
                order_no=f"SO-{suffix[:30]}-{index}",
                store_id=store.id,
                status="PENDING_PAYMENT",
                payment_status="PENDING",
                subtotal="10.00",
                total_amount="10.00",
                discount_amount="0.00",
                coupon_discount="0.00",
                voucher_discount="0.00",
                packing_fee="0.00",
                dining_type="TAKEOUT",
            )
            for index in (1, 2)
        ]
        setup.add_all(orders)
        await setup.flush()
        order_items = []
        for order_index, order in enumerate(orders):
            item_products = products if order_index == 0 else list(reversed(products))
            for product in item_products:
                order_items.append(
                    OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        product_name_snapshot=product.name,
                        unit_price_snapshot="5.00",
                        quantity=1,
                        subtotal="5.00",
                    )
                )
        setup.add_all(order_items)
        order_ids = [o.id for o in orders]
        store_id = store.id
        product_ids = [p.id for p in products]
        stock_ids = [row.id for row in stock_rows]
        await setup.commit()

    async with maker() as session_a, maker() as session_b:
        order_a = await session_a.get(Order, order_ids[0])
        order_b = await session_b.get(Order, order_ids[1])
        assert order_a is not None and order_b is not None
        lower_product_id = min(product_ids)
        first_stock_lock_acquired = asyncio.Event()
        allow_session_a_to_continue = asyncio.Event()
        second_session_waiting_for_first_product = asyncio.Event()
        original_restore = stock_service.restore_stock

        async def pause_after_first_lock(db, product_id, qty, *, store_id=None):
            result = await original_restore(db, product_id, qty, store_id=store_id)
            if db is session_a and int(product_id) == lower_product_id:
                first_stock_lock_acquired.set()
                await allow_session_a_to_continue.wait()
            return result

        monkeypatch.setattr(stock_service, "restore_stock", pause_after_first_lock)
        session_a_connection = (await session_a.connection()).sync_connection

        def observe_second_session_lock_attempt(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is not session_a_connection
                and "SELECT PRODUCTS.STORE_ID, PRODUCTS.STATUS" in normalized
                and "FOR UPDATE" in normalized
            ):
                second_session_waiting_for_first_product.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_session_lock_attempt)
        try:
            async def cancel_and_commit(db, order):
                await order_lifecycle.apply_order_status(
                    db, order, "CANCELLED", source="SYSTEM", assert_kind="SYSTEM"
                )
                await db.commit()

            task_a = asyncio.create_task(cancel_and_commit(session_a, order_a))
            await asyncio.wait_for(first_stock_lock_acquired.wait(), timeout=10)
            task_b = asyncio.create_task(cancel_and_commit(session_b, order_b))
            # Session B reaches the same lowest Product FOR UPDATE while A holds it.
            await asyncio.wait_for(second_session_waiting_for_first_product.wait(), timeout=10)
            allow_session_a_to_continue.set()
            await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=30)
        finally:
            allow_session_a_to_continue.set()
            event.remove(engine.sync_engine, "before_cursor_execute", observe_second_session_lock_attempt)
            for task in (locals().get("task_a"), locals().get("task_b")):
                if task is not None and not task.done():
                    task.cancel()
            if session_a.in_transaction():
                await session_a.rollback()
            if session_b.in_transaction():
                await session_b.rollback()

    async with maker() as verify:
        for product_id, stock_id in zip(product_ids, stock_ids):
            stock_row = await verify.scalar(
                select(StoreProductStock).where(StoreProductStock.id == stock_id)
            )
            product = await verify.get(Product, product_id)
            assert stock_row is not None and stock_row.stock == 7
            assert product is not None and product.sales_count == 0

        await verify.execute(
            delete(OutboxEvent).where(
                OutboxEvent.aggregate_type == "delivery_stock",
                OutboxEvent.aggregate_id.in_(stock_ids),
            )
        )
        await verify.execute(delete(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        await verify.execute(
            delete(OrderStatusHistory).where(OrderStatusHistory.order_id.in_(order_ids))
        )
        await verify.execute(delete(Order).where(Order.id.in_(order_ids)))
        await verify.execute(delete(StoreProductStock).where(StoreProductStock.id.in_(stock_ids)))
        await verify.execute(delete(Product).where(Product.id.in_(product_ids)))
        await verify.execute(delete(ProductCategory).where(ProductCategory.id == category.id))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_outbox_enqueue_duplicate_preserves_both_outer_transactions(db_session):
    """A duplicate-key SAVEPOINT race returns one row without poisoning either transaction."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    from app.services.outbox_worker import enqueue_outbox

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    aggregate_id = int(uuid.uuid4().hex[:12], 16)
    aggregate_type = "mysql_outbox_race"
    event_kind = "same_event"
    second_insert_attempted = asyncio.Event()

    async with maker() as session_a, maker() as session_b:
        await session_a.connection()
        connection_a = (await session_a.connection()).sync_connection

        row_a, is_new_a = await enqueue_outbox(
            session_a,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_kind=event_kind,
            payload={"writer": "a"},
        )
        assert is_new_a is True
        row_a_id = int(row_a.id)
        session_a.add(
            OutboxEvent(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_kind="writer_a_probe",
                payload='{"writer":"a"}',
                status="PENDING",
            )
        )

        def observe_duplicate_insert(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if conn is not connection_a and "INSERT INTO OUTBOX_EVENTS" in normalized:
                second_insert_attempted.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_duplicate_insert)
        try:
            async def enqueue_from_second_transaction():
                row, is_new = await enqueue_outbox(
                    session_b,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_kind=event_kind,
                    payload={"writer": "b"},
                )
                session_b.add(
                    OutboxEvent(
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        event_kind="writer_b_probe",
                        payload='{"writer":"b"}',
                        status="PENDING",
                    )
                )
                await session_b.flush()
                return int(row.id), is_new

            task_b = asyncio.create_task(enqueue_from_second_transaction())
            # The competing INSERT has reached MySQL while transaction A still
            # owns the unique key. Commit A to release the duplicate-key wait.
            await asyncio.wait_for(second_insert_attempted.wait(), timeout=10)
            await session_a.commit()
            row_b_id, is_new_b = await asyncio.wait_for(task_b, timeout=20)
            await session_b.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_duplicate_insert)
            if session_a.in_transaction():
                await session_a.rollback()
            if session_b.in_transaction():
                await session_b.rollback()

    async with maker() as verify:
        same_key_rows = (
            await verify.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                    OutboxEvent.event_kind == event_kind,
                )
            )
        ).scalars().all()
        assert len(same_key_rows) == 1
        assert same_key_rows[0].id == row_a_id == row_b_id
        assert is_new_b is False

        probe_kinds = (
            await verify.execute(
                select(OutboxEvent.event_kind).where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                    OutboxEvent.event_kind.in_(("writer_a_probe", "writer_b_probe")),
                )
            )
        ).scalars().all()
        assert set(probe_kinds) == {"writer_a_probe", "writer_b_probe"}

        await verify.execute(
            delete(OutboxEvent).where(
                OutboxEvent.aggregate_type == aggregate_type,
                OutboxEvent.aggregate_id == aggregate_id,
            )
        )
        await verify.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_outbox_enqueue_duplicate_preserves_both_outer_transactions(db_session):
    """A duplicate-key SAVEPOINT race returns one row without poisoning either transaction."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    from app.services.outbox_worker import enqueue_outbox

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    aggregate_id = int(uuid.uuid4().hex[:12], 16)
    aggregate_type = "mysql_outbox_race"
    event_kind = "same_event"
    second_insert_attempted = asyncio.Event()

    async with maker() as session_a, maker() as session_b:
        await session_a.connection()
        connection_a = (await session_a.connection()).sync_connection

        row_a, is_new_a = await enqueue_outbox(
            session_a,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_kind=event_kind,
            payload={"writer": "a"},
        )
        assert is_new_a is True
        row_a_id = int(row_a.id)
        session_a.add(
            OutboxEvent(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_kind="writer_a_probe",
                payload='{"writer":"a"}',
                status="PENDING",
            )
        )

        def observe_duplicate_insert(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if conn is not connection_a and "INSERT INTO OUTBOX_EVENTS" in normalized:
                second_insert_attempted.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_duplicate_insert)
        try:
            async def enqueue_from_second_transaction():
                row, is_new = await enqueue_outbox(
                    session_b,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_kind=event_kind,
                    payload={"writer": "b"},
                )
                session_b.add(
                    OutboxEvent(
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        event_kind="writer_b_probe",
                        payload='{"writer":"b"}',
                        status="PENDING",
                    )
                )
                await session_b.flush()
                return int(row.id), is_new

            task_b = asyncio.create_task(enqueue_from_second_transaction())
            # The competing INSERT has reached MySQL while transaction A still
            # owns the unique key. Commit A to release the duplicate-key wait.
            await asyncio.wait_for(second_insert_attempted.wait(), timeout=10)
            await session_a.commit()
            row_b_id, is_new_b = await asyncio.wait_for(task_b, timeout=20)
            await session_b.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_duplicate_insert)
            if session_a.in_transaction():
                await session_a.rollback()
            if session_b.in_transaction():
                await session_b.rollback()

    async with maker() as verify:
        same_key_rows = (
            await verify.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                    OutboxEvent.event_kind == event_kind,
                )
            )
        ).scalars().all()
        assert len(same_key_rows) == 1
        assert same_key_rows[0].id == row_a_id == row_b_id
        assert is_new_b is False

        probe_kinds = (
            await verify.execute(
                select(OutboxEvent.event_kind).where(
                    OutboxEvent.aggregate_type == aggregate_type,
                    OutboxEvent.aggregate_id == aggregate_id,
                    OutboxEvent.event_kind.in_(("writer_a_probe", "writer_b_probe")),
                )
            )
        ).scalars().all()
        assert set(probe_kinds) == {"writer_a_probe", "writer_b_probe"}

        await verify.execute(
            delete(OutboxEvent).where(
                OutboxEvent.aggregate_type == aggregate_type,
                OutboxEvent.aggregate_id == aggregate_id,
            )
        )
        await verify.commit()
