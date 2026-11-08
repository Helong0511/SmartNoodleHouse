from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from app.services.menu_cache_service import enqueue_menu_cache_invalidation
from app.services.outbox_policy import CACHE_INVALIDATION_MAX_ATTEMPTS
from app.services.outbox_worker import claim_outbox_item
from reco_factories import make_customer


@pytest.mark.asyncio
async def test_menu_cache_invalidation_outbox_does_not_dead_letter_on_transient_redis_outage(
    db_session,
):
    store_id = 981234

    await enqueue_menu_cache_invalidation(db_session, store_id)
    await db_session.flush()

    rows = list(
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "menu_cache",
                    OutboxEvent.event_kind == "invalidate",
                )
            )
        ).scalars().all()
    )
    row = next(
        item for item in rows
        if item.payload and json.loads(item.payload).get("store_id") == store_id
    )

    assert row.status == "PENDING"
    assert row.max_attempts == CACHE_INVALIDATION_MAX_ATTEMPTS
    assert row.max_attempts == 2_147_483_647


@pytest.mark.asyncio
async def test_exhausted_legacy_cache_invalidation_can_be_claimed_after_retry_budget_upgrade(
    db_session,
):
    row = OutboxEvent(
        aggregate_type="menu_cache",
        aggregate_id=981235,
        event_kind="invalidate",
        payload=json.dumps({"store_id": 981235}),
        status="FAILED",
        attempt_count=8,
        max_attempts=CACHE_INVALIDATION_MAX_ATTEMPTS,
        next_retry_at=None,
    )
    db_session.add(row)
    await db_session.flush()

    claimed = await claim_outbox_item(db_session, row.id, worker="retry-budget-test")

    assert claimed is not None
    assert claimed.status == "PROCESSING"
    assert claimed.attempt_count == 9
    assert claimed.max_attempts == CACHE_INVALIDATION_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_reco_invalidation_outbox_uses_durable_retry_budget(db_session, monkeypatch):
    from app.services import reco_cache

    customer = await make_customer(db_session)
    spawned = []

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(reco_cache, "get_redis", redis_unavailable)
    monkeypatch.setattr(reco_cache, "spawn_bg", lambda coro: spawned.append(coro))

    customer.recommend_opt_in = 0
    reco_cache.defer_invalidate_customer(db_session, customer.id)
    await db_session.flush()

    pending = list(
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "reco_cache",
                    OutboxEvent.aggregate_id == customer.id,
                )
            )
        ).scalars().all()
    )
    assert pending
    assert all(row.event_kind.startswith("invalidate_") for row in pending)
    assert all(row.status == "PENDING" for row in pending)
    assert all(row.max_attempts == CACHE_INVALIDATION_MAX_ATTEMPTS for row in pending)
    assert all(json.loads(row.payload or "{}") == {"customer_id": customer.id} for row in pending)

    await db_session.commit()

    persisted_customer = await db_session.get(type(customer), customer.id)
    assert persisted_customer is not None
    assert persisted_customer.recommend_opt_in == 0
    persisted = list(
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "reco_cache",
                    OutboxEvent.aggregate_id == customer.id,
                )
            )
        ).scalars().all()
    )
    assert len(persisted) == len(pending)
    assert all(row.status == "PENDING" for row in persisted)

    for coroutine in spawned:
        coroutine.close()
