"""Regression tests for expired flash-sale claim replacement transaction ordering."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select

from app.core.exceptions import AppException
from app.models import (
    Customer,
    FlashSaleClaim,
    FlashSaleConfig,
    FlashSaleStock,
    MarketingCampaign,
    Product,
    ProductCategory,
    Store,
)
from app.services import flash_sale_service


@pytest.mark.mysql
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "remaining", "expect_success"),
    [
        (1, 3, False),  # quota reached: old CLAIMED row already consumes the only unit
        (2, 0, False),  # quota available, but conditional stock deduction fails
        (2, 1, True),   # replacement succeeds: old row and new row commit together
    ],
)
async def test_expired_claim_transition_is_atomic_with_replacement(
    db_session, monkeypatch, limit, remaining, expect_success
):
    """Failed checks must not leave a flushed EXPIRED state; success must persist it."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    now = flash_sale_service.naive_local_now()
    monkeypatch.setattr(flash_sale_service, "naive_local_now", lambda: now)

    suffix = uuid.uuid4().hex[:12]
    store = Store(store_no=f"FS-{suffix}", name="Flash Sale Test", status=1)
    db_session.add(store)
    await db_session.flush()

    category = ProductCategory(
        store_id=store.id, category_no=f"CAT-{suffix}", name="Test", status=1
    )
    db_session.add(category)
    await db_session.flush()

    product = Product(
        store_id=store.id,
        category_id=category.id,
        product_no=f"PROD-{suffix}",
        name="Test noodle",
        price=Decimal("0.00"),
        status="ON_SALE",
    )
    customer = Customer(customer_no=f"CUST-FS-{suffix}")
    db_session.add_all([product, customer])
    await db_session.flush()

    campaign = MarketingCampaign(
        store_id=store.id,
        code=f"FS-{suffix}",
        code_key=f"{store.id}:FS-{suffix}",
        name="Flash Sale Test",
        campaign_type="FLASH_SALE",
        status="RUNNING",
        schedule_mode="ABSOLUTE",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
    )
    db_session.add(campaign)
    await db_session.flush()

    config = FlashSaleConfig(
        campaign_id=campaign.id,
        product_id=product.id,
        per_customer_limit=limit,
        claim_valid_minutes=30,
    )
    stock = FlashSaleStock(
        campaign_id=campaign.id,
        store_id=store.id,
        product_id=product.id,
        total_quantity=3,
        remaining_quantity=remaining,
    )
    old_claim = FlashSaleClaim(
        campaign_id=campaign.id,
        store_id=store.id,
        customer_id=customer.id,
        unit_no=1,
        claim_no=f"OLD-{suffix}",
        status="CLAIMED",
        claimed_at=now - timedelta(hours=2),
        expires_at=now - timedelta(minutes=1),
    )
    db_session.add_all([config, stock, old_claim])
    await db_session.flush()
    campaign_id = int(campaign.id)
    store_id = int(store.id)
    customer_id = int(customer.id)
    old_claim_id = int(old_claim.id)
    stock_id = int(stock.id)
    await db_session.commit()

    if expect_success:
        result = await flash_sale_service.FlashSaleService.claim(
            db_session,
            campaign_id=campaign_id,
            store_id=store_id,
            customer_id=customer_id,
        )
        assert result["status"] == "CLAIMED"
        await db_session.commit()
    else:
        with pytest.raises(AppException):
            await flash_sale_service.FlashSaleService.claim(
                db_session,
                campaign_id=campaign_id,
                store_id=store_id,
                customer_id=customer_id,
            )
        # The API/request transaction rolls back after the service exception.
        await db_session.rollback()

    persisted_old = await db_session.scalar(
        select(FlashSaleClaim).where(FlashSaleClaim.id == old_claim_id)
    )
    persisted_stock = await db_session.scalar(
        select(FlashSaleStock).where(FlashSaleStock.id == stock_id)
    )
    assert persisted_stock is not None

    if expect_success:
        assert persisted_old.status == "EXPIRED"
        assert persisted_stock.remaining_quantity == remaining - 1
        new_claims = list(
            (
                await db_session.execute(
                    select(FlashSaleClaim).where(
                        FlashSaleClaim.campaign_id == campaign_id,
                        FlashSaleClaim.customer_id == customer_id,
                        FlashSaleClaim.id != old_claim_id,
                    )
                )
            ).scalars()
        )
        assert len(new_claims) == 1
        assert new_claims[0].unit_no == 2
    else:
        assert persisted_old.status == "CLAIMED"
        assert persisted_stock.remaining_quantity == remaining
        new_claim_count = await db_session.scalar(
            select(FlashSaleClaim.id).where(
                FlashSaleClaim.campaign_id == campaign_id,
                FlashSaleClaim.customer_id == customer_id,
                FlashSaleClaim.id != old_claim_id,
            )
        )
        assert new_claim_count is None
