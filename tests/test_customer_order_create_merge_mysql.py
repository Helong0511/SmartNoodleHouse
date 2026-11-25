"""MySQL regression: customer order creation must reject a pre-authenticated customer merged concurrently."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer, Order, Product, ProductCategory, Store
from app.schemas.order import OrderCreate
from app.services import customer_merge_service as customer_merge_service_module
from app.services.customer_merge_service import CustomerMergeService
from app.api.orders import create_order as create_customer_order
from app.services.order_create_service import OrderCreateService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_customer_order_create_rejects_stale_customer_after_real_merge(db_session):
    """The real customer endpoint must wait on the lifecycle gate and fail closed."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"CO-{suffix[:20]}", name="Customer order merge gate race")
        source = Customer(customer_no=f"COS-{suffix[:27]}")
        target = Customer(customer_no=f"COT-{suffix[:27]}")
        setup.add_all([store, source, target])
        await setup.flush()
        store_id, source_id, target_id = int(store.id), int(source.id), int(target.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as order_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/customer order create concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            with patch(
                "app.api.orders.AuditService.log",
                new_callable=AsyncMock,
            ):
                attempt = asyncio.create_task(
                    create_customer_order(
                        OrderCreate(
                            store_id=store_id,
                            items=[{"product_id": 1, "quantity": 1}],
                            idempotency_key=f"customer-order-{suffix[:24]}",
                            dining_type="TAKEAWAY",
                        ),
                        request=SimpleNamespace(headers={}, state=SimpleNamespace(request_id=None)),
                        db=order_db,
                        customer=SimpleNamespace(id=source_id),
                    )
                )
                await asyncio.wait_for(gate_waiting.wait(), timeout=10)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(attempt, timeout=15)
                assert exc.value.status_code == 401
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if order_db.in_transaction():
                await order_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source_row = await verify.get(Customer, source_id)
        assert source_row is not None and source_row.account_status == "DISABLED"
        count = await verify.scalar(
            select(func.count()).select_from(Order).where(
                Order.store_id == store_id,
                Order.customer_id == source_id,
            )
        )
        assert count == 0
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_customer_order_create_commits_before_real_merge(db_session):
    """If create owns the gate first, its committed order must be migrated by merge."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"COW-{suffix[:20]}", name="Order create wins merge gate race")
        source = Customer(customer_no=f"COWS-{suffix[:27]}")
        target = Customer(customer_no=f"COWT-{suffix[:27]}")
        setup.add_all([store, source, target])
        await setup.flush()
        category = ProductCategory(
            store_id=store.id,
            category_no=f"COWC-{suffix[:16]}",
            name="Concurrency test category",
        )
        setup.add(category)
        await setup.flush()
        product = Product(
            store_id=store.id,
            category_id=category.id,
            product_no=f"COWP-{suffix[:16]}",
            name="Concurrency test noodle",
            price=Decimal("12.00"),
            status="ON_SALE",
        )
        setup.add(product)
        await setup.flush()
        store_id, source_id, target_id, product_id = (
            int(store.id), int(source.id), int(target.id), int(product.id)
        )
        await setup.commit()

    create_at_commit = asyncio.Event()
    allow_create_commit = asyncio.Event()
    merge_waiting_for_gate = asyncio.Event()

    async with maker() as merge_db, maker() as order_db:
        original_commit = order_db.commit
        async def pause_create_commit():
            create_at_commit.set()
            await asyncio.wait_for(allow_create_commit.wait(), timeout=15)
            await original_commit()
        order_db.commit = pause_create_commit
        order_connection = (await order_db.connection()).sync_connection

        def observe_merge_gate_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if conn is not order_connection and "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
                merge_waiting_for_gate.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_merge_gate_wait)
        try:
            with patch("app.api.orders.AuditService.log", new_callable=AsyncMock):
                create_task = asyncio.create_task(
                    create_customer_order(
                        OrderCreate(
                            store_id=store_id,
                            items=[{"product_id": product_id, "quantity": 1}],
                            idempotency_key=f"customer-order-wins-{suffix[:18]}",
                            dining_type="TAKEAWAY",
                        ),
                        request=SimpleNamespace(headers={}, state=SimpleNamespace(request_id=None)),
                        db=order_db,
                        customer=SimpleNamespace(id=source_id),
                    )
                )
                await asyncio.wait_for(create_at_commit.wait(), timeout=15)

                async def merge_after_create_gate():
                    with patch(
                        "app.services.customer_merge_service.AuditService.log",
                        new_callable=AsyncMock,
                    ):
                        result = await CustomerMergeService.merge(
                            merge_db,
                            target_customer_id=target_id,
                            source_customer_id=source_id,
                            reason="MySQL order-create-wins lifecycle gate regression",
                        )
                    await merge_db.commit()
                    return result

                merge_task = asyncio.create_task(merge_after_create_gate())
                await asyncio.wait_for(merge_waiting_for_gate.wait(), timeout=10)
                allow_create_commit.set()
                await asyncio.wait_for(create_task, timeout=30)
                result = await asyncio.wait_for(merge_task, timeout=30)
                assert int(result["source_customer_id"]) == source_id
        finally:
            allow_create_commit.set()
            event.remove(engine.sync_engine, "before_cursor_execute", observe_merge_gate_wait)
            for task in (locals().get("create_task"), locals().get("merge_task")):
                if task is not None and not task.done():
                    task.cancel()
            if order_db.in_transaction():
                await order_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source_row = await verify.get(Customer, source_id)
        target_row = await verify.get(Customer, target_id)
        assert source_row is not None and source_row.account_status == "DISABLED"
        assert target_row is not None and target_row.account_status == "ACTIVE"
        scoped_key = OrderCreateService._scoped_idempotency_key(
            f"customer-order-wins-{suffix[:18]}",
            channel="H5",
            customer_id=source_id,
            store_id=store_id,
            operator_id=None,
        )
        order = await verify.scalar(
            select(Order).where(Order.idempotency_key == scoped_key)
        )
        assert order is not None
        assert int(order.customer_id) == target_id


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_opposite_direction_customer_merges_serialize_and_reject_disabled_target(db_session):
    """Opposite-direction merges lock gates in one order; the loser cannot target a disabled account."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        lower = Customer(customer_no=f"MGLO-{suffix[:27]}")
        higher = Customer(customer_no=f"MGHI-{suffix[:27]}")
        setup.add_all([lower, higher])
        await setup.flush()
        lower_id, higher_id = int(lower.id), int(higher.id)
        assert lower_id < higher_id
        await setup.commit()

    first_gate_acquired = asyncio.Event()
    release_first_merge = asyncio.Event()
    second_merge_waiting = asyncio.Event()

    async with maker() as merge_a_db, maker() as merge_b_db:
        connection_a = (await merge_a_db.connection()).sync_connection
        original_lock = customer_merge_service_module.lock_customer_write_gates

        async def pause_first_after_gates(db, customer_ids):
            locked_ids = await original_lock(db, customer_ids)
            if db is merge_a_db:
                first_gate_acquired.set()
                await asyncio.wait_for(release_first_merge.wait(), timeout=20)
            return locked_ids

        def observe_second_gate_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if conn is not connection_a and "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
                second_merge_waiting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_gate_wait)
        try:
            with patch.object(
                customer_merge_service_module,
                "lock_customer_write_gates",
                new=pause_first_after_gates,
            ), patch(
                "app.services.customer_merge_service.AuditService.log",
                new_callable=AsyncMock,
            ):
                async def run_merge(db, target_id, source_id):
                    result = await CustomerMergeService.merge(
                        db,
                        target_customer_id=target_id,
                        source_customer_id=source_id,
                        reason="MySQL opposite-direction merge serialization regression",
                    )
                    await db.commit()
                    return result

                first_task = asyncio.create_task(run_merge(merge_a_db, lower_id, higher_id))
                await asyncio.wait_for(first_gate_acquired.wait(), timeout=10)
                second_task = asyncio.create_task(run_merge(merge_b_db, higher_id, lower_id))
                await asyncio.wait_for(second_merge_waiting.wait(), timeout=10)
                release_first_merge.set()
                first_result, second_result = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task, return_exceptions=True),
                    timeout=30,
                )
                assert isinstance(first_result, dict)
                assert isinstance(second_result, AppException)
                assert second_result.code == 40062
                assert second_result.status_code == 400
        finally:
            release_first_merge.set()
            event.remove(engine.sync_engine, "before_cursor_execute", observe_second_gate_wait)
            for task in (locals().get("first_task"), locals().get("second_task")):
                if task is not None and not task.done():
                    task.cancel()
            if merge_a_db.in_transaction():
                await merge_a_db.rollback()
            if merge_b_db.in_transaction():
                await merge_b_db.rollback()

    async with maker() as verify:
        lower_row = await verify.get(Customer, lower_id)
        higher_row = await verify.get(Customer, higher_id)
        assert lower_row is not None and lower_row.status == 1
        assert lower_row.account_status == "ACTIVE"
        assert higher_row is not None and higher_row.status == 0
        assert higher_row.account_status == "DISABLED"
