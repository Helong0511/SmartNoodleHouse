"""Real MySQL concurrency regression for order terminal transitions."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Order, OrderItem, OrderStatusHistory, OutboxEvent, Product, ProductCategory, Store, StoreProductStock
from app.services import order_lifecycle, stock_service
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
