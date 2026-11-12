"""Real MySQL concurrency regression for order terminal transitions."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Order, OutboxEvent, Store
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
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
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
