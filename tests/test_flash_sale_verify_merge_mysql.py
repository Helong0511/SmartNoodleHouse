"""MySQL regression: real Flash Sale verification waits behind a real customer merge."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer, FlashSaleClaim, MarketingCampaign, Store
from app.services.customer_merge_service import CustomerMergeService
from app.services.flash_sale_service import FlashSaleService


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_verify_observes_claim_cancelled_by_real_merge(db_session):
    """The verifier must wait for the owner gate, then reject the merged claim."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        target = Customer(customer_no=f"FSM-T-{suffix[:26]}")
        source = Customer(customer_no=f"FSM-S-{suffix[:26]}")
        store = Store(store_no=f"FSM-{suffix[:20]}", name="merge-race-test")
        setup.add_all([target, source, store])
        await setup.flush()

        campaign = MarketingCampaign(
            store_id=int(store.id),
            code=f"FSM-{suffix[:20]}",
            code_key=f"{int(store.id)}:FSM-{suffix[:20]}",
            name="merge-race-test",
            campaign_type="FLASH_SALE",
            status="RUNNING",
        )
        setup.add(campaign)
        await setup.flush()

        claim = FlashSaleClaim(
            campaign_id=int(campaign.id),
            store_id=int(store.id),
            customer_id=int(source.id),
            unit_no=1,
            claim_no=f"FSM{suffix[:24]}",
            status="CLAIMED",
            claimed_at=datetime.now(),
            expires_at=datetime.now() + timedelta(hours=1),
        )
        setup.add(claim)
        await setup.flush()
        target_id, source_id = int(target.id), int(source.id)
        store_id, campaign_id, claim_id = int(store.id), int(campaign.id), int(claim.id)
        claim_no = claim.claim_no
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as verify_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            result = await CustomerMergeService.merge(
                merge_db,
                target_customer_id=target_id,
                source_customer_id=source_id,
                reason="MySQL merge/flash verify concurrency regression",
            )
        assert int(result["source_customer_id"]) == source_id
        assert result["stats"]["flash_sale_claims_cancelled"] == 1

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate)
        try:
            verify_task = asyncio.create_task(
                FlashSaleService.verify(
                    verify_db,
                    claim_no=claim_no,
                    store_id=store_id,
                    operator_id=None,
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)
            await merge_db.commit()

            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(verify_task, timeout=15)
            assert exc.value.status_code == 400
            assert "已取消" in str(exc.value.message if hasattr(exc.value, "message") else exc.value)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate)
            if verify_db.in_transaction():
                await verify_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as cleanup:
        await cleanup.execute(delete(FlashSaleClaim).where(FlashSaleClaim.id == claim_id))
        await cleanup.execute(delete(MarketingCampaign).where(MarketingCampaign.id == campaign_id))
        await cleanup.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await cleanup.execute(delete(Store).where(Store.id == store_id))
        await cleanup.commit()
