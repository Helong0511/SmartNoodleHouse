"""Real MySQL concurrency regression for cumulative points clawback."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Order, Payment, PaymentRefund, PointLedger, Store
from app.services.customer_service import CustomerService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_successful_refunds_claw_back_cumulative_points_once(
    db_session, monkeypatch
):
    """Customer row fencing prevents two refund workers applying the same cumulative target."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"PT-{suffix[:20]}", name="Points refund concurrency")
        setup.add(store)
        await setup.flush()
        customer = Customer(
            customer_no=f"PTC-{suffix[:20]}",
            nickname="Points refund concurrency",
            points=100,
        )
        setup.add(customer)
        await setup.flush()
        order = Order(
            order_no=f"PTO-{suffix[:30]}",
            customer_id=customer.id,
            store_id=store.id,
            status="PARTIAL_REFUND",
            payment_status="PARTIAL_REFUND",
            subtotal=Decimal("100.00"),
            total_amount=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            coupon_discount=Decimal("0.00"),
            voucher_discount=Decimal("0.00"),
            packing_fee=Decimal("0.00"),
            dining_type="TAKEOUT",
        )
        setup.add(order)
        await setup.flush()
        payment = Payment(
            payment_no=f"PTP-{suffix[:30]}",
            order_id=order.id,
            customer_id=customer.id,
            provider="WECHAT",
            channel="WECHAT_MINIPROGRAM",
            amount=Decimal("100.00"),
            status="PAID",
        )
        setup.add(payment)
        await setup.flush()
        refund_a = PaymentRefund(
            refund_no=f"PTR-A-{suffix[:24]}",
            payment_id=payment.id,
            order_id=order.id,
            amount=Decimal("25.00"),
            status="SUCCESS",
        )
        refund_b = PaymentRefund(
            refund_no=f"PTR-B-{suffix[:24]}",
            payment_id=payment.id,
            order_id=order.id,
            amount=Decimal("25.00"),
            status="SUCCESS",
        )
        setup.add_all([refund_a, refund_b])
        await setup.flush()
        setup.add(
            PointLedger(
                customer_id=customer.id,
                order_id=order.id,
                refund_id=None,
                delta=100,
                balance_after=100,
                entry_type="EARN",
            )
        )
        customer_id, order_id = customer.id, order.id
        refund_a_id, refund_b_id = refund_a.id, refund_b.id
        await setup.commit()

    monkeypatch.setattr(
        CustomerService, "recalculate_member_level", AsyncMock()
    )
    second_lock_reached = asyncio.Event()

    def observe_second_customer_lock(_conn, _cursor, statement, _params, _ctx, _many):
        normalized = statement.upper()
        if "FOR UPDATE" in normalized and "CUSTOMERS.ID" in normalized:
            second_lock_reached.set()

    async with maker() as first, maker() as second:
        order_a = await first.get(Order, order_id)
        order_b = await second.get(Order, order_id)
        assert order_a is not None and order_b is not None

        # First worker owns the customer row lock and stages the cumulative
        # 50-point clawback, but deliberately does not commit yet.
        deducted_a = await CustomerService.deduct_points_for_refund(
            first, order_a, Decimal("25.00"), refund_id=refund_a_id
        )
        assert deducted_a == 50

        event.listen(engine.sync_engine, "before_cursor_execute", observe_second_customer_lock)
        try:
            competing = asyncio.create_task(
                CustomerService.deduct_points_for_refund(
                    second, order_b, Decimal("25.00"), refund_id=refund_b_id
                )
            )
            await asyncio.wait_for(second_lock_reached.wait(), timeout=10)
            await first.commit()
            deducted_b = await asyncio.wait_for(competing, timeout=20)
            assert deducted_b == 0
            await second.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_second_customer_lock)
            if first.in_transaction():
                await first.rollback()
            if second.in_transaction():
                await second.rollback()

    async with maker() as verify:
        customer = await verify.get(Customer, customer_id)
        assert customer is not None
        assert customer.points == 50
        clawbacks = (
            await verify.execute(
                select(PointLedger).where(
                    PointLedger.order_id == order_id,
                    PointLedger.entry_type == "REFUND_DEDUCT",
                )
            )
        ).scalars().all()
        assert len(clawbacks) == 2
        assert sum(int(row.delta) for row in clawbacks) == -50
        assert sorted(int(row.delta) for row in clawbacks) == [-50, 0]

        await verify.execute(delete(PointLedger).where(PointLedger.order_id == order_id))
        await verify.execute(delete(PaymentRefund).where(PaymentRefund.order_id == order_id))
        await verify.execute(delete(Payment).where(Payment.order_id == order_id))
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(Customer).where(Customer.id == customer_id))
        store_id = (
            await verify.scalar(select(Store.id).where(Store.store_no == f"PT-{suffix[:20]}"))
        )
        if store_id:
            await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()
