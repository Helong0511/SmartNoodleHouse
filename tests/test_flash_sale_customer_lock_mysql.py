"""MySQL regression for flash-sale claims racing customer merge."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer
from app.services.flash_sale_service import _lock_active_customer


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_customer_lock_reloads_disabled_state_after_merge_commit(db_session):
    """A stale authenticated ORM instance must not pass the claim ownership gate."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        customer = Customer(customer_no=f"FSC-{suffix[:30]}")
        setup.add(customer)
        await setup.flush()
        customer_id = int(customer.id)
        await setup.commit()

    competing_lock = asyncio.Event()

    def observe_lock(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "FOR UPDATE" in normalized and "CUSTOMERS" in normalized:
            competing_lock.set()

    async with maker() as merge_db, maker() as claim_db:
        merge_customer = await merge_db.get(Customer, customer_id)
        stale_claim_customer = await claim_db.get(Customer, customer_id)
        assert merge_customer is not None and stale_claim_customer is not None
        assert stale_claim_customer.status == 1
        assert stale_claim_customer.account_status == "ACTIVE"

        merge_customer.status = 0
        merge_customer.account_status = "DISABLED"
        await merge_db.flush()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_lock)
        try:
            claim_task = asyncio.create_task(_lock_active_customer(claim_db, customer_id))
            await asyncio.wait_for(competing_lock.wait(), timeout=10)
            await merge_db.commit()
            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(claim_task, timeout=15)
            assert exc.value.status_code == 401
            # populate_existing=True must refresh the stale identity-map object.
            assert stale_claim_customer.status == 0
            assert stale_claim_customer.account_status == "DISABLED"
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_lock)
            if claim_db.in_transaction():
                await claim_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as cleanup:
        await cleanup.execute(delete(Customer).where(Customer.id == customer_id))
        await cleanup.commit()
