"""MySQL regression for member activation racing customer merge."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer
from app.services.customer_service import CustomerService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_member_activation_rechecks_customer_after_merge_gate_wait(db_session):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        customer = Customer(customer_no=f"ACT-{suffix[:30]}")
        setup.add(customer)
        await setup.flush()
        customer_id = int(customer.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as activation_db:
        merge_customer = await merge_db.get(Customer, customer_id)
        stale_customer = await activation_db.get(Customer, customer_id)
        assert merge_customer is not None and stale_customer is not None
        assert stale_customer.status == 1

        # Simulate merge holding the lifecycle gate and disabling the source account.
        from app.services.customer_write_gate import lock_customer_write_gates

        await lock_customer_write_gates(merge_db, [customer_id])
        merge_customer.status = 0
        merge_customer.account_status = "DISABLED"
        await merge_db.flush()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate)
        try:
            task = asyncio.create_task(CustomerService.activate_member(activation_db, stale_customer))
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(task, timeout=15)
            assert exc.value.status_code == 401
            assert stale_customer.status == 0
            assert stale_customer.account_status == "DISABLED"
            assert stale_customer.member_status != "MEMBER"
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate)
            if activation_db.in_transaction():
                await activation_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as cleanup:
        await cleanup.execute(delete(Customer).where(Customer.id == customer_id))
        await cleanup.commit()
