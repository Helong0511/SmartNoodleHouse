"""MySQL regression: recharge creation endpoint must reject a customer merged concurrently."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.wallet import RechargeCreate, create_recharge
from app.core.exceptions import AppException
from app.models import Customer, Payment, RechargeOrder, RechargePackage, Store
from app.services.customer_merge_service import CustomerMergeService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_recharge_create_endpoint_rejects_customer_after_real_merge(db_session):
    """The real wallet endpoint must acquire the gate before creating RechargeOrder."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"RC-{suffix[:20]}", name="Recharge create merge gate race")
        source = Customer(
            customer_no=f"RCS-{suffix[:27]}",
            member_status="MEMBER",
        )
        target = Customer(
            customer_no=f"RCT-{suffix[:27]}",
            member_status="MEMBER",
        )
        setup.add_all([store, source, target])
        await setup.flush()
        package = RechargePackage(
            store_id=store.id,
            name=f"Concurrency package {suffix[:8]}",
            pay_amount=Decimal("10.00"),
            gift_amount=Decimal("1.00"),
            status=1,
        )
        setup.add(package)
        await setup.flush()
        store_id, source_id, target_id, package_id = (
            int(store.id), int(source.id), int(target.id), int(package.id)
        )
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    fake_payment = SimpleNamespace(
        payment_no=f"FAKE-{suffix[:24]}",
        amount=Decimal("10.00"),
    )
    fake_payment_creation = AsyncMock(return_value=(fake_payment, {"payment_payload": "test"}))

    async with maker() as merge_db, maker() as recharge_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/recharge create endpoint concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            with patch(
                "app.services.payment_service.PaymentService.create_recharge_payment",
                new=fake_payment_creation,
            ):
                attempt = asyncio.create_task(
                    create_recharge(
                        RechargeCreate(
                            package_id=package_id,
                            store_id=store_id,
                            provider="WECHAT",
                            channel_type="native",
                            idempotency_key=f"recharge-{suffix[:24]}",
                        ),
                        request=SimpleNamespace(state=SimpleNamespace(request_id=None)),
                        db=recharge_db,
                        customer=SimpleNamespace(id=source_id),
                    )
                )
                await asyncio.wait_for(gate_waiting.wait(), timeout=10)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(attempt, timeout=15)
                assert exc.value.status_code == 401
                fake_payment_creation.assert_not_awaited()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if recharge_db.in_transaction():
                await recharge_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source_row = await verify.get(Customer, source_id)
        assert source_row is not None and source_row.account_status == "DISABLED"
        count = await verify.scalar(
            select(func.count()).select_from(RechargeOrder).where(
                RechargeOrder.customer_id == source_id,
                RechargeOrder.idempotency_key == f"recharge-{suffix[:24]}",
            )
        )
        assert count == 0
        await verify.execute(delete(RechargeOrder).where(RechargeOrder.customer_id.in_([source_id, target_id])))
        await verify.execute(delete(RechargePackage).where(RechargePackage.id == package_id))
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_recharge_create_commits_before_real_merge(db_session):
    """Recharge/payment creation commits first; merge must migrate both owners."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"RCW-{suffix[:20]}", name="Recharge create wins merge gate race")
        source = Customer(customer_no=f"RCWS-{suffix[:27]}", member_status="MEMBER")
        target = Customer(customer_no=f"RCWT-{suffix[:27]}", member_status="MEMBER")
        setup.add_all([store, source, target])
        await setup.flush()
        package = RechargePackage(
            store_id=store.id,
            name=f"Create wins package {suffix[:8]}",
            pay_amount=Decimal("10.00"),
            gift_amount=Decimal("1.00"),
            status=1,
        )
        setup.add(package)
        await setup.flush()
        store_id, source_id, target_id, package_id = (
            int(store.id), int(source.id), int(target.id), int(package.id)
        )
        await setup.commit()

    provider_started = asyncio.Event()
    allow_provider_return = asyncio.Event()

    class PausedProvider:
        async def create_payment(self, payment, _proxy_order, _extra=None):
            # PaymentService has already committed RechargeOrder + Payment and
            # released the lifecycle gate before it invokes the provider.
            provider_started.set()
            await asyncio.wait_for(allow_provider_return.wait(), timeout=20)
            return {
                "provider": "WECHAT",
                "payment_no": payment.payment_no,
                "code_url": f"weixin://test/{payment.payment_no}",
                "amount": str(payment.amount),
            }

    async with maker() as merge_db, maker() as recharge_db:
        with patch(
            "app.services.payment_service.PaymentService.get_provider",
            return_value=PausedProvider(),
        ):
            create_task = asyncio.create_task(
                create_recharge(
                    RechargeCreate(
                        package_id=package_id,
                        store_id=store_id,
                        provider="WECHAT",
                        channel_type="native",
                        idempotency_key=f"recharge-wins-{suffix[:18]}",
                    ),
                    request=SimpleNamespace(),
                    db=recharge_db,
                    customer=SimpleNamespace(id=source_id),
                )
            )
            try:
                await asyncio.wait_for(provider_started.wait(), timeout=20)
                # The service's internal commit has completed before provider
                # creation starts, so merge now races only with post-commit work.
                with patch(
                    "app.services.customer_merge_service.AuditService.log",
                    new_callable=AsyncMock,
                ):
                    result = await CustomerMergeService.merge(
                        merge_db,
                        target_customer_id=target_id,
                        source_customer_id=source_id,
                        reason="MySQL recharge-create-wins lifecycle gate regression",
                    )
                await merge_db.commit()
                assert int(result["source_customer_id"]) == source_id
                allow_provider_return.set()
                await asyncio.wait_for(create_task, timeout=30)
            finally:
                allow_provider_return.set()
                if not create_task.done():
                    create_task.cancel()
                if recharge_db.in_transaction():
                    await recharge_db.rollback()
                if merge_db.in_transaction():
                    await merge_db.rollback()

    async with maker() as verify:
        recharge = await verify.scalar(
            select(RechargeOrder).where(
                RechargeOrder.idempotency_key == f"recharge-wins-{suffix[:18]}"
            )
        )
        assert recharge is not None
        assert int(recharge.customer_id) == target_id
        payment = await verify.scalar(
            select(Payment).where(Payment.recharge_order_id == recharge.id)
        )
        assert payment is not None
        assert int(payment.customer_id) == target_id
        assert int(recharge.customer_id) == int(payment.customer_id)
