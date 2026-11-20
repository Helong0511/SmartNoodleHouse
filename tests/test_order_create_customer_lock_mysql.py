"""MySQL regression for ordinary order creation racing customer merge."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer
from app.services.order_create_service import _lock_active_order_customer


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_order_create_customer_gate_reloads_disabled_state_after_merge_commit(db_session):
    """A request authenticated before merge must not create a source-owned order."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        customer = Customer(customer_no=f"OCC-{suffix[:30]}")
        setup.add(customer)
        await setup.flush()
        customer_id = int(customer.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "FOR UPDATE" in normalized and "CUSTOMERS" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as order_db:
        merge_customer = await merge_db.get(Customer, customer_id)
        stale_order_customer = await order_db.get(Customer, customer_id)
        assert merge_customer is not None and stale_order_customer is not None
        assert stale_order_customer.status == 1
        assert stale_order_customer.account_status == "ACTIVE"

        merge_customer.status = 0
        merge_customer.account_status = "DISABLED"
        await merge_db.flush()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate)
        try:
            gate_task = asyncio.create_task(
                _lock_active_order_customer(order_db, customer_id)
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(gate_task, timeout=15)
            assert exc.value.status_code == 401
            assert stale_order_customer.status == 0
            assert stale_order_customer.account_status == "DISABLED"
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate)
            if order_db.in_transaction():
                await order_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as cleanup:
        await cleanup.execute(delete(Customer).where(Customer.id == customer_id))
        await cleanup.commit()
