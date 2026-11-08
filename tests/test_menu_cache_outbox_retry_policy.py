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


@pytest.mark.asyncio
async def test_reco_profile_change_and_outbox_roll_back_together(db_session):
    from app.services import reco_cache

    customer = await make_customer(db_session)
    customer_id = customer.id
    # Keep the pre-existing customer outside the transaction under test.
    await db_session.commit()

    customer.recommend_opt_in = 0
    reco_cache.defer_invalidate_customer(db_session, customer_id)
    await db_session.flush()
    await db_session.rollback()

    restored_customer = await db_session.get(type(customer), customer_id)
    assert restored_customer is not None
    assert restored_customer.recommend_opt_in == 1

    persisted = list(
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "reco_cache",
                    OutboxEvent.aggregate_id == customer_id,
                )
            )
        ).scalars().all()
    )
    assert persisted == []


@pytest.mark.asyncio
async def test_reco_invalidation_outbox_handler_raises_when_redis_is_unavailable(
    db_session, monkeypatch
):
    from types import SimpleNamespace

    from app.services import reco_cache

    customer = await make_customer(db_session)

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(reco_cache, "get_redis", redis_unavailable)

    key = ("reco_cache", "invalidate")
    previous = reco_cache.OUTBOX_HANDLERS.get(key) if hasattr(reco_cache, "OUTBOX_HANDLERS") else None
    from app.services.outbox_worker import OUTBOX_HANDLERS, _resolve_outbox_handler

    previous = OUTBOX_HANDLERS.get(key)
    reco_cache.register_reco_cache_outbox_handler()
    try:
        row = SimpleNamespace(
            aggregate_type="reco_cache",
            aggregate_id=customer.id,
            event_kind="invalidate_test-event",
            payload=json.dumps({"customer_id": customer.id}),
        )
        handler = _resolve_outbox_handler(row)
        assert handler is not None

        with pytest.raises(ConnectionError, match="redis unavailable"):
            await handler(db_session, row)
    finally:
        if previous is None:
            OUTBOX_HANDLERS.pop(key, None)
        else:
            OUTBOX_HANDLERS[key] = previous
