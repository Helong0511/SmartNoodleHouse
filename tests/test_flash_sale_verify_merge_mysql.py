"""MySQL regression: real Flash Sale verification waits behind a real customer merge."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.models import Customer, FlashSaleClaim, MarketingCampaign, Store
from app.services.customer_merge_service import CustomerMergeService
from app.services.flash_sale_service import FlashSaleService
from app.core.response import naive_local_now


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
            claimed_at=naive_local_now(),
            expires_at=naive_local_now() + timedelta(hours=1),
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
            # Merge disables the source customer; verify fails closed at the customer gate
            # before it can expose the claim cancellation state to a stale identity.
            assert exc.value.status_code == 401
            assert "顾客不存在或已禁用" in str(exc.value.message if hasattr(exc.value, "message") else exc.value)
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



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_verify_commits_before_real_customer_merge_preserves_used_history(db_session):
    """If verification wins the gate, merge preserves USED claim history and moves its order."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    from decimal import Decimal
    from sqlalchemy import select
    from app.models import (
        FlashSaleConfig,
        FlashSaleStock,
        Order,
        OrderItem,
        OrderStatusHistory,
        Payment,
        Product,
        ProductCategory,
    )

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        target = Customer(customer_no=f"FSW-T-{suffix[:26]}")
        source = Customer(customer_no=f"FSW-S-{suffix[:26]}")
        store = Store(store_no=f"FSW-{suffix[:20]}", name="verify-wins-merge-race", status=1)
        setup.add_all([target, source, store])
        await setup.flush()

        category = ProductCategory(
            store_id=int(store.id),
            category_no=f"FSW-C-{suffix[:16]}",
            name="Flash verify test",
            status=1,
        )
        setup.add(category)
        await setup.flush()
        product = Product(
            store_id=int(store.id),
            category_id=int(category.id),
            product_no=f"FSW-P-{suffix[:16]}",
            name="Concurrency noodle",
            price=Decimal("0.00"),
            stock=5,
            status="ON_SALE",
        )
        setup.add(product)
        await setup.flush()

        campaign = MarketingCampaign(
            store_id=int(store.id),
            code=f"FSW-{suffix[:18]}",
            code_key=f"{int(store.id)}:FSW-{suffix[:18]}",
            name="verify-wins-merge-race",
            campaign_type="FLASH_SALE",
            status="RUNNING",
        )
        setup.add(campaign)
        await setup.flush()
        config = FlashSaleConfig(
            campaign_id=int(campaign.id),
            product_id=int(product.id),
            per_customer_limit=1,
            claim_valid_minutes=30,
        )
        stock = FlashSaleStock(
            campaign_id=int(campaign.id),
            store_id=int(store.id),
            product_id=int(product.id),
            total_quantity=1,
            remaining_quantity=1,
        )
        claim = FlashSaleClaim(
            campaign_id=int(campaign.id),
            store_id=int(store.id),
            customer_id=int(source.id),
            unit_no=1,
            claim_no=f"FSW{suffix[:24]}",
            status="CLAIMED",
            claimed_at=naive_local_now(),
            expires_at=naive_local_now() + timedelta(hours=1),
        )
        setup.add_all([config, stock, claim])
        await setup.flush()
        target_id, source_id = int(target.id), int(source.id)
        store_id, campaign_id = int(store.id), int(campaign.id)
        category_id, product_id = int(category.id), int(product.id)
        claim_id, claim_no = int(claim.id), claim.claim_no
        await setup.commit()

    merge_waiting = asyncio.Event()

    def observe_merge_gate(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            merge_waiting.set()

    async with maker() as verify_db, maker() as merge_db:
        verified = await FlashSaleService.verify(
            verify_db,
            claim_no=claim_no,
            store_id=store_id,
            operator_id=None,
        )
        assert verified["status"] == "USED"
        order_id = int(verified["order_id"])

        event.listen(engine.sync_engine, "before_cursor_execute", observe_merge_gate)
        try:
            with patch(
                "app.services.customer_merge_service.AuditService.log",
                new_callable=AsyncMock,
            ):
                merge_task = asyncio.create_task(
                    CustomerMergeService.merge(
                        merge_db,
                        target_customer_id=target_id,
                        source_customer_id=source_id,
                        reason="MySQL flash verify wins before customer merge",
                    )
                )
                await asyncio.wait_for(merge_waiting.wait(), timeout=10)
                await verify_db.commit()
                merged = await asyncio.wait_for(merge_task, timeout=15)
            assert merged["stats"]["flash_sale_claims_cancelled"] == 0
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_merge_gate)
            if verify_db.in_transaction():
                await verify_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        persisted_claim = await verify.get(FlashSaleClaim, claim_id)
        persisted_order = await verify.get(Order, order_id)
        source_row = await verify.get(Customer, source_id)
        assert persisted_claim is not None
        assert persisted_claim.status == "USED"
        assert int(persisted_claim.customer_id) == source_id
        assert int(persisted_claim.order_id) == order_id
        assert persisted_order is not None and int(persisted_order.customer_id) == target_id
        assert source_row is not None and source_row.account_status == "DISABLED"
        payment = await verify.scalar(select(Payment).where(Payment.order_id == order_id))
        assert payment is not None and int(payment.customer_id) == target_id

        await verify.execute(delete(Payment).where(Payment.order_id == order_id))
        await verify.execute(delete(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id))
        await verify.execute(delete(OrderItem).where(OrderItem.order_id == order_id))
        await verify.execute(delete(Order).where(Order.id == order_id))
        await verify.execute(delete(FlashSaleClaim).where(FlashSaleClaim.id == claim_id))
        await verify.execute(delete(FlashSaleStock).where(FlashSaleStock.campaign_id == campaign_id))
        await verify.execute(delete(FlashSaleConfig).where(FlashSaleConfig.campaign_id == campaign_id))
        await verify.execute(delete(MarketingCampaign).where(MarketingCampaign.id == campaign_id))
        await verify.execute(delete(Product).where(Product.id == product_id))
        await verify.execute(delete(ProductCategory).where(ProductCategory.id == category_id))
        await verify.execute(delete(Customer).where(Customer.id.in_([source_id, target_id])))
        await verify.execute(delete(Store).where(Store.id == store_id))
        await verify.commit()
