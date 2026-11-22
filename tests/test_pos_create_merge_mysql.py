"""MySQL regression: POS order creation must reject a customer merged concurrently."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer, Order, Store
from app.schemas.order import PosOrderCreate
from app.services.customer_merge_service import CustomerMergeService
from app.api.orders_pos import create_pos_order


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_pos_create_rejects_stale_customer_after_real_merge(db_session):
    """The actual POS endpoint waits on the customer gate and creates no stale-owner order."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"PC-{suffix[:20]}", name="POS merge gate race")
        source = Customer(customer_no=f"PCS-{suffix[:27]}")
        target = Customer(customer_no=f"PCT-{suffix[:27]}")
        setup.add_all([store, source, target])
        await setup.flush()
        store_id, source_id, target_id = int(store.id), int(source.id), int(target.id)
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async def fake_open_shift(*_args, **_kwargs):
        return None

    async with maker() as merge_db, maker() as pos_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/POS create concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            with patch(
                "app.services.pos_shift_service.PosShiftService.require_open_shift",
                new=AsyncMock(side_effect=fake_open_shift),
            ):
                attempt = asyncio.create_task(
                    create_pos_order(
                        PosOrderCreate(
                            store_id=store_id,
                            customer_id=source_id,
                            items=[{"product_id": 1, "quantity": 1}],
                            dining_type="TAKEAWAY",
                        ),
                        request=SimpleNamespace(),
                        db=pos_db,
                        user=SimpleNamespace(
                            role=SimpleNamespace(code="STORE_MANAGER"),
                            store_id=store_id,
                            id=None,
                        ),
                    )
                )
                await asyncio.wait_for(gate_waiting.wait(), timeout=10)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(attempt, timeout=15)
                assert exc.value.status_code == 401
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if pos_db.in_transaction():
                await pos_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source_row = await verify.get(Customer, source_id)
        assert source_row is not None and source_row.account_status == "DISABLED"
        count = await verify.scalar(
            select(func.count()).select_from(Order).where(
                Order.store_id == store_id,
                Order.customer_id == source_id,
                Order.channel == "POS",
            )
        )
        assert count == 0
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()
