"""MySQL regression for the real customer merge racing member activation."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer
from app.services.customer_merge_service import CustomerMergeService
from app.services.customer_service import CustomerService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_member_activation_rechecks_customer_after_real_merge_gate_wait(db_session):
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        target = Customer(customer_no=f"ACT-T-{suffix[:27]}")
        source = Customer(customer_no=f"ACT-S-{suffix[:27]}")
        setup.add_all([target, source])
        await setup.flush()
        target_id, source_id = int(target.id), int(source.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as activation_db:
        stale_customer = await activation_db.get(Customer, source_id)
        assert stale_customer is not None and stale_customer.status == 1

        # Exercise the production merge service, not a hand-written owner/status mutation.
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/activation concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id
        assert result["stats"]["flash_sale_claims_cancelled"] == 0

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate)
        try:
            task = asyncio.create_task(
                CustomerService.activate_member(activation_db, stale_customer)
            )
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
        await cleanup.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await cleanup.commit()
