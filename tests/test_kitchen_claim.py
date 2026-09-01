"""厨房软认领：开始制作认领；他人需接管。"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.exceptions import AppException
from app.models import Order, Store, User
from app.services.customer_service import OrderCreateService
from app.services.kitchen_claim import apply_kitchen_claim_for_status, takeover_kitchen_claim


@pytest.fixture(autouse=True)
def _skip_order_side_effects(monkeypatch):
    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr("app.services.order_lifecycle.record_order_status", noop)
    monkeypatch.setattr("app.services.order_lifecycle.notify_order_ready_subscribe", noop)


@pytest.mark.asyncio
async def test_start_preparing_claims_order(db_session):
    store = Store(id=801, store_no="T-CLAIM-01", name="认领店", status=1)
    cook = User(
        id=802,
        username="cook_a",
        password_hash="x",
        display_name="张三",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    order = Order(
        id=803,
        order_no="O-CLAIM-01",
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=datetime.now(timezone.utc),
    )
    db_session.add_all([store, cook, order])
    await db_session.commit()

    updated = await OrderCreateService.update_status(
        db_session, order.id, "PREPARING", operator_id=cook.id
    )
    await db_session.commit()
    assert updated.status == "PREPARING"
    assert updated.kitchen_claimed_by == cook.id
    assert updated.kitchen_claimed_at is not None


@pytest.mark.asyncio
async def test_other_cook_blocked_without_takeover(db_session):
    store = Store(id=811, store_no="T-CLAIM-02", name="认领店2", status=1)
    cook_a = User(
        id=812,
        username="cook_a2",
        password_hash="x",
        display_name="张三",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    cook_b = User(
        id=813,
        username="cook_b2",
        password_hash="x",
        display_name="李四",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    order = Order(
        id=814,
        order_no="O-CLAIM-02",
        store_id=store.id,
        status="PREPARING",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        kitchen_claimed_by=cook_a.id,
        kitchen_claimed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([store, cook_a, cook_b, order])
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await apply_kitchen_claim_for_status(
            db_session, order, "COOKING", operator_id=cook_b.id, force_takeover=False
        )
    assert ei.value.code == 40028
    assert "张三" in ei.value.message

    updated = await OrderCreateService.update_status(
        db_session,
        order.id,
        "COOKING",
        operator_id=cook_b.id,
        force_takeover=True,
    )
    await db_session.commit()
    assert updated.status == "COOKING"
    assert updated.kitchen_claimed_by == cook_b.id


@pytest.mark.asyncio
async def test_takeover_without_status_change(db_session):
    store = Store(id=821, store_no="T-CLAIM-03", name="认领店3", status=1)
    cook_a = User(
        id=822,
        username="cook_a3",
        password_hash="x",
        display_name="张三",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    cook_b = User(
        id=823,
        username="cook_b3",
        password_hash="x",
        display_name="李四",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    order = Order(
        id=824,
        order_no="O-CLAIM-03",
        store_id=store.id,
        status="COOKING",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        kitchen_claimed_by=cook_a.id,
        kitchen_claimed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([store, cook_a, cook_b, order])
    await db_session.commit()

    await takeover_kitchen_claim(db_session, order, operator_id=cook_b.id)
    await db_session.commit()
    refreshed = await db_session.get(Order, order.id)
    assert refreshed.status == "COOKING"
    assert refreshed.kitchen_claimed_by == cook_b.id


@pytest.mark.asyncio
async def test_completed_clears_claim(db_session):
    store = Store(id=831, store_no="T-CLAIM-04", name="认领店4", status=1)
    cook = User(
        id=832,
        username="cook_a4",
        password_hash="x",
        display_name="张三",
        role_id=1,
        store_id=store.id,
        status=1,
    )
    order = Order(
        id=833,
        order_no="O-CLAIM-04",
        store_id=store.id,
        status="READY",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        kitchen_claimed_by=cook.id,
        kitchen_claimed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([store, cook, order])
    await db_session.commit()

    updated = await OrderCreateService.update_status(
        db_session, order.id, "COMPLETED", operator_id=cook.id
    )
    await db_session.commit()
    assert updated.status == "COMPLETED"
    assert updated.kitchen_claimed_by is None
    assert updated.kitchen_claimed_at is None
