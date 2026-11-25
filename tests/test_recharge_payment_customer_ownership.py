"""Regression contracts for recharge/payment ownership across customer merges."""
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.api import wallet
from app.services.payment_service import PaymentService


def test_recharge_payment_ownership_comes_from_locked_recharge_order():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    assert "payment.customer_id = int(recharge.customer_id)" in source
    assert "customer_id=int(recharge.customer_id)" in source
    assert "payment.customer_id = customer_id" not in source
    assert "customer_id=customer_id or recharge.customer_id" not in source


def test_pending_recharge_payment_is_locked_after_recharge_order():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    recharge_lock = source.index("select(RechargeOrder)")
    pending_query = source.index("Payment.recharge_order_id == recharge.id")
    pending_lock = source.index(".with_for_update()", pending_query)
    assert recharge_lock < pending_query < pending_lock


def test_recharge_response_does_not_disclose_merged_target_wallet():
    source = inspect.getsource(wallet.create_recharge)
    owner_check = source.index("int(ro.customer_id) == int(customer.id)")
    wallet_snapshot = source.index("BalanceService.snapshot(cust)", owner_check)
    assert owner_check < wallet_snapshot


def test_recharge_payment_acquires_customer_gate_before_parent_row_lock():
    source = inspect.getsource(PaymentService.create_recharge_payment)
    gate = source.index("await lock_customer_write_gates(db, [int(observed_owner_id)])")
    locked_recharge = source.index(".with_for_update()", source.index("locked = ("))
    assert gate < locked_recharge


def test_recharge_creation_acquires_customer_gate_before_creating_recharge_order():
    source = inspect.getsource(wallet.create_recharge)
    gate = source.index("lock_active_customer_for_write")
    package_lookup = source.index("pkg = await db.get")
    assert gate < package_lookup


def test_recharge_settlement_acquires_gate_before_recharge_and_payment_locks():
    source = inspect.getsource(PaymentService._settle_recharge_payment)
    gate = source.index("await lock_customer_write_gates")
    recharge_lock = source.index(".with_for_update()")
    payment_lock = source.index("select(Payment)")
    assert gate < recharge_lock < payment_lock


def test_recharge_callback_and_query_defer_parent_lock_to_settlement():
    callback = inspect.getsource(PaymentService._apply_recharge_callback)
    query = inspect.getsource(PaymentService._settle_from_query)
    assert "SimpleNamespace(id=int(payment.recharge_order_id))" in callback
    assert "SimpleNamespace(id=int(payment.recharge_order_id))" in query
    assert ".with_for_update()" not in callback[callback.index('if parsed["status"] == "PAID"'):callback.index("await cls._settle_recharge_payment")]
    assert ".with_for_update()" not in query[query.index("# 储值充值单"):query.index("await cls._settle_recharge_payment")]


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_recharge_payment_creation_aborts_after_real_customer_merge(db_session):
    """A stale payment request must not create a payment after the real merge moves its recharge."""
    import asyncio
    import uuid

    from sqlalchemy import delete, event, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.exceptions import AppException
    from app.models import Customer, Payment, RechargeOrder, Store
    from app.services.customer_merge_service import CustomerMergeService

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"RG-{suffix[:20]}", name="Recharge gate race")
        source = Customer(customer_no=f"RGS-{suffix[:28]}")
        target = Customer(customer_no=f"RGT-{suffix[:28]}")
        setup.add_all([store, source, target])
        await setup.flush()
        recharge = RechargeOrder(
            recharge_no=f"RGR-{suffix[:30]}",
            customer_id=source.id,
            store_id=store.id,
            pay_amount="10.00",
            credit_principal="10.00",
            credit_gift="0.00",
            status="PENDING",
            channel="WECHAT",
        )
        setup.add(recharge)
        await setup.flush()
        source_id, target_id = int(source.id), int(target.id)
        recharge_id, store_id = int(recharge.id), int(store.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as payment_db:
        stale_recharge = await payment_db.get(RechargeOrder, recharge_id)
        assert stale_recharge is not None
        assert int(stale_recharge.customer_id) == source_id

        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/recharge payment concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id
        assert result["stats"]["recharge_orders"] == 1

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            attempt = asyncio.create_task(
                PaymentService.create_recharge_payment(
                    payment_db,
                    recharge=stale_recharge,
                    provider="WECHAT",
                    customer_id=source_id,
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(attempt, timeout=15)
            assert exc.value.code == 40906
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if payment_db.in_transaction():
                await payment_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        current = await verify.get(RechargeOrder, recharge_id)
        source_row = await verify.get(Customer, source_id)
        assert current is not None and int(current.customer_id) == target_id
        assert source_row is not None and source_row.account_status == "DISABLED"
        count = await verify.scalar(
            select(func.count()).select_from(Payment).where(
                Payment.recharge_order_id == recharge_id
            )
        )
        assert count == 0
        await verify.execute(delete(RechargeOrder).where(RechargeOrder.id == recharge_id))
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_recharge_settlement_aborts_after_real_customer_merge(db_session):
    """A stale provider settlement must not credit a recharge moved by a real merge."""
    import asyncio
    import uuid

    from sqlalchemy import delete, event, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.exceptions import AppException
    from app.models import Customer, Payment, RechargeOrder, Store
    from app.services.customer_merge_service import CustomerMergeService

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"RS-{suffix[:20]}", name="Recharge settlement gate race")
        source = Customer(customer_no=f"RSS-{suffix[:27]}")
        target = Customer(customer_no=f"RST-{suffix[:27]}")
        setup.add_all([store, source, target])
        await setup.flush()
        recharge = RechargeOrder(
            recharge_no=f"RSR-{suffix[:29]}",
            customer_id=source.id,
            store_id=store.id,
            pay_amount="10.00",
            credit_principal="10.00",
            credit_gift="0.00",
            status="PENDING",
            channel="WECHAT",
        )
        setup.add(recharge)
        await setup.flush()
        payment = Payment(
            payment_no=f"RSP-{suffix[:29]}",
            recharge_order_id=recharge.id,
            customer_id=source.id,
            provider="WECHAT",
            channel="MINIPROGRAM",
            amount="10.00",
            status="PENDING",
        )
        setup.add(payment)
        await setup.flush()
        source_id, target_id = int(source.id), int(target.id)
        recharge_id, payment_id, store_id = int(recharge.id), int(payment.id), int(store.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as settlement_db:
        stale_recharge = await settlement_db.get(RechargeOrder, recharge_id)
        stale_payment = await settlement_db.get(Payment, payment_id)
        assert stale_recharge is not None and stale_payment is not None
        assert int(stale_recharge.customer_id) == source_id

        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/recharge settlement concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id
        assert result["stats"]["recharge_orders"] == 1

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            attempt = asyncio.create_task(
                PaymentService._settle_recharge_payment(
                    settlement_db,
                    payment=stale_payment,
                    recharge=stale_recharge,
                    provider="WECHAT",
                    source="MYSQL_MERGE_RACE_TEST",
                    provider_trade_no=f"trade-{suffix[:24]}",
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(attempt, timeout=15)
            assert exc.value.code == 40906
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if settlement_db.in_transaction():
                await settlement_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        current_recharge = await verify.get(RechargeOrder, recharge_id)
        current_payment = await verify.get(Payment, payment_id)
        source_row = await verify.get(Customer, source_id)
        assert current_recharge is not None
        assert int(current_recharge.customer_id) == target_id
        assert current_recharge.status == "PENDING"
        assert current_payment is not None and current_payment.status == "PENDING"
        assert source_row is not None and source_row.account_status == "DISABLED"
        await verify.execute(delete(Payment).where(Payment.id == payment_id))
        await verify.execute(delete(RechargeOrder).where(RechargeOrder.id == recharge_id))
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()


def test_order_payment_uses_locked_order_owner_and_acquires_gate_before_order_lock():
    source = inspect.getsource(PaymentService.create_payment)
    gate = source.index("await lock_customer_write_gates(db, [int(observed_owner_id)])")
    order_lock = source.index(".with_for_update()", source.index("locked = ("))
    assert gate < order_lock
    assert "payment.customer_id = order.customer_id" in source
    assert "customer_id=order.customer_id" in source
    assert "customer_id=customer_id or order.customer_id" not in source
    assert "recharge.customer_id" not in source


def test_order_payment_revalidates_request_owner_after_locked_order_refresh():
    source = inspect.getsource(PaymentService.create_payment)
    locked_refresh = source.index(".execution_options(populate_existing=True)")
    owner_check = source.index("if customer_id is not None and int(customer_id) != locked_owner_id")
    assert locked_refresh < owner_check

def test_refund_success_acquires_customer_gate_before_order_and_payment_locks():
    source = inspect.getsource(PaymentService._lock_order_then_payment_for_refund)
    gate = source.index("await lock_customer_write_gates(db, [int(observed_owner_id)])")
    order_lock = source.index(".with_for_update()")
    payment_lock = source.index("select(Payment)")
    owner_revalidation = source.index("if locked_owner_id != observed_owner_id")
    assert gate < order_lock < payment_lock
    assert order_lock < owner_revalidation < payment_lock

@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_refund_parent_lock_waits_for_merge_gate_without_order_gate_deadlock(db_session):
    """Refund parent locking must join the gate-first protocol used by customer merge."""
    import asyncio
    import uuid
    from decimal import Decimal
    from types import SimpleNamespace

    from sqlalchemy import delete, event, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.exceptions import AppException
    from app.models import Customer, Order, Payment, Product, ProductCategory, Store
    from app.services.customer_merge_service import CustomerMergeService
    from app.services.order_create_service import OrderCreateService

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"RF-{suffix[:20]}", name="Refund merge gate race")
        source = Customer(customer_no=f"RFS-{suffix[:27]}")
        target = Customer(customer_no=f"RFT-{suffix[:27]}")
        setup.add_all([store, source, target])
        await setup.flush()
        category = ProductCategory(
            store_id=store.id,
            category_no=f"RFC-{suffix[:16]}",
            name="Refund merge race category",
        )
        setup.add(category)
        await setup.flush()
        product = Product(
            store_id=store.id,
            category_id=category.id,
            product_no=f"RFP-{suffix[:16]}",
            name="Refund merge race product",
            price=Decimal("10.00"),
            status="ON_SALE",
        )
        setup.add(product)
        await setup.flush()
        order = await OrderCreateService.create_order(
            setup,
            store_id=int(store.id),
            table_token=None,
            customer_id=int(source.id),
            items=[{"product_id": int(product.id), "quantity": 1}],
            channel="POS",
            dining_type="TAKEAWAY",
        )
        payment = Payment(
            payment_no=f"RFPAY-{suffix[:24]}",
            order_id=order.id,
            customer_id=source.id,
            provider="BALANCE",
            channel="BALANCE",
            amount=Decimal("10.00"),
            status="PAID",
        )
        setup.add(payment)
        await setup.flush()
        source_id, target_id = int(source.id), int(target.id)
        order_id, payment_id, store_id = int(order.id), int(payment.id), int(store.id)
        product_id, category_id = int(product.id), int(category.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as refund_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/refund lock-order regression",
            )
        assert int(result["source_customer_id"]) == source_id

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            attempt = asyncio.create_task(
                PaymentService._lock_order_then_payment_for_refund(
                    refund_db,
                    SimpleNamespace(order_id=order_id, payment_id=payment_id),
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(attempt, timeout=15)
            assert exc.value.code == 40906
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if refund_db.in_transaction():
                await refund_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        persisted_order = await verify.get(Order, order_id)
        persisted_payment = await verify.get(Payment, payment_id)
        assert persisted_order is not None and int(persisted_order.customer_id) == target_id
        assert persisted_payment is not None and int(persisted_payment.customer_id) == target_id
        await verify.execute(delete(Payment).where(Payment.id == payment_id))
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Product).where(Product.id == product_id))
        await verify.execute(delete(ProductCategory).where(ProductCategory.id == category_id))
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()

