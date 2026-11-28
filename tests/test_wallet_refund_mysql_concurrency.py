"""Real MySQL REPEATABLE READ regression for cumulative wallet refunds."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import BalanceLedger, Customer, Order, Payment, PaymentRefund, Store
from app.services.balance_service import BalanceService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_split_wallet_refunds_see_committed_restore_rows_after_stale_snapshot(db_session):
    """A waiting refund must use a current read, not its pre-lock RR snapshot."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB REPEATABLE READ")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"WR-{suffix[:20]}", name="Wallet refund race")
        setup.add(store)
        await setup.flush()
        customer = Customer(
            customer_no=f"WC-{suffix[:30]}",
            display_name="Wallet refund race",
            balance_principal=Decimal("0.00"),
            balance_gift=Decimal("0.00"),
        )
        setup.add(customer)
        await setup.flush()
        order = Order(
            order_no=f"WO-{suffix[:30]}",
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
        payment = Payment(
            payment_no=f"WPAY-{suffix[:30]}",
            order_id=order.id,
            customer_id=customer.id,
            provider="MOCK",
            channel="MOCK",
            amount=Decimal("10.00"),
            currency="CNY",
            status="PAID",
        )
        setup.add(payment)
        await setup.flush()
        refund_rows = [
            PaymentRefund(
                refund_no=f"WREF-{suffix[:25]}-{index}",
                payment_id=payment.id,
                order_id=order.id,
                amount=Decimal("3.33"),
                status="SUCCESS",
            )
            for index in (1, 2)
        ]
        setup.add_all(refund_rows)
        await setup.flush()
        refund_ids = [row.id for row in refund_rows]
        payment_id = payment.id
        store_id, customer_id, order_id = store.id, customer.id, order.id
        setup.add(
            BalanceLedger(
                customer_id=customer_id,
                store_id=store_id,
                order_id=order_id,
                entry_type="PAY",
                delta_principal=Decimal("-7.00"),
                delta_gift=Decimal("-3.00"),
                principal_after=Decimal("0.00"),
                gift_after=Decimal("0.00"),
                biz_no=f"WP-{suffix[:30]}",
            )
        )
        await setup.commit()

    async with maker() as first, maker() as second:
        # Session A owns the serialization row before session B establishes its
        # REPEATABLE READ snapshot and starts waiting for the same customer.
        await first.execute(
            select(Customer).where(Customer.id == customer_id).with_for_update()
        )
        # This ordinary consistent read establishes B's old snapshot while A
        # still holds the customer lock. There are not yet any restore rows.
        stale_rows = (
            await second.execute(
                select(BalanceLedger).where(
                    BalanceLedger.entry_type == "REFUND_RESTORE",
                    BalanceLedger.order_id == order_id,
                    BalanceLedger.customer_id == customer_id,
                )
            )
        ).scalars().all()
        assert stale_rows == []

        second_refund = asyncio.create_task(
            BalanceService.restore_for_refund(
                second, customer_id=customer_id, order_id=order_id,
                refund_id=refund_ids[1], amount=Decimal("3.33"), store_id=store_id,
            )
        )
        wait_sql = text("""
            SELECT COUNT(*) FROM performance_schema.data_lock_waits AS w
            JOIN performance_schema.data_locks AS req
              ON req.ENGINE = w.ENGINE
             AND req.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
            WHERE req.OBJECT_SCHEMA = DATABASE()
              AND req.OBJECT_NAME = 'customers'
        """)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        observed_wait = False
        try:
            async with engine.connect() as observer:
                while loop.time() < deadline:
                    waiting = int((await observer.execute(wait_sql)).scalar_one())
                    if waiting > 0:
                        observed_wait = True
                        break
                    if second_refund.done():
                        await second_refund
                        break
                    await asyncio.sleep(0.05)
            assert observed_wait, "InnoDB data_lock_waits did not show competing customer lock"
            assert not second_refund.done(), "second refund passed customer lock before first commit"
            first_refund = await BalanceService.restore_for_refund(
                first,
                customer_id=customer_id,
                order_id=order_id,
                refund_id=refund_ids[0],
                amount=Decimal("3.33"),
                store_id=store_id,
            )
            assert first_refund.delta_gift == Decimal("1.00")
            assert first_refund.delta_principal == Decimal("2.33")
            await first.commit()

            second_row = await asyncio.wait_for(second_refund, timeout=15)
            assert second_row.delta_gift == Decimal("1.00")
            assert second_row.delta_principal == Decimal("2.33")
            await second.commit()
        finally:
            if not second_refund.done():
                await first.rollback()
                try:
                    await asyncio.wait_for(second_refund, timeout=3)
                except Exception:
                    second_refund.cancel()
            if first.in_transaction():
                await first.rollback()
            if second.in_transaction():
                await second.rollback()

    async with maker() as verify:
        customer = await verify.get(Customer, customer_id)
        restores = (
            await verify.execute(
                select(BalanceLedger).where(
                    BalanceLedger.entry_type == "REFUND_RESTORE",
                    BalanceLedger.order_id == order_id,
                    BalanceLedger.customer_id == customer_id,
                ).order_by(BalanceLedger.refund_id)
            )
        ).scalars().all()
        assert len(restores) == 2
        assert sum((row.delta_principal for row in restores), Decimal("0.00")) == Decimal("4.66")
        assert sum((row.delta_gift for row in restores), Decimal("0.00")) == Decimal("2.00")
        assert customer.balance_principal == Decimal("4.66")
        assert customer.balance_gift == Decimal("2.00")
        assert customer.balance_principal + customer.balance_gift == Decimal("6.66")

        await verify.execute(
            delete(BalanceLedger).where(BalanceLedger.customer_id == customer_id)
        )
        await verify.execute(delete(PaymentRefund).where(PaymentRefund.id.in_(refund_ids)))
        await verify.execute(delete(Payment).where(Payment.id == payment_id))
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Customer).where(Customer.id == customer_id))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()
