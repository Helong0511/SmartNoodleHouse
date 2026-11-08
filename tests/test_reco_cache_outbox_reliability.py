from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from app.services import reco_cache
from app.services.outbox_policy import CACHE_INVALIDATION_MAX_ATTEMPTS
from app.services.outbox_worker import OUTBOX_HANDLERS, _resolve_outbox_handler
from reco_factories import make_customer


@pytest.mark.asyncio
async def test_reco_invalidation_outbox_is_durable_and_unique_per_update(db_session, monkeypatch):
    customer = await make_customer(db_session)
    spawned = []

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    # The profile mutation and its Outbox records share this database transaction.
    monkeypatch.setattr(reco_cache, "get_redis", redis_unavailable)
    monkeypatch.setattr(reco_cache, "spawn_bg", lambda coro: spawned.append(coro))

    customer.recommend_opt_in = 0
    reco_cache.defer_invalidate_customer(db_session, customer.id)
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
    assert len(pending) == 2
    assert len({row.event_kind for row in pending}) == 2
    assert all(row.event_kind.startswith("invalidate_") for row in pending)
    assert all(row.status == "PENDING" for row in pending)
    assert all(row.max_attempts == CACHE_INVALIDATION_MAX_ATTEMPTS for row in pending)
    assert all(json.loads(row.payload or "{}") == {"customer_id": customer.id} for row in pending)

    await db_session.commit()

    # The business change and invalidation events survive together despite Redis outage.
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
    assert len(persisted) == 2
    assert all(row.status == "PENDING" for row in persisted)

    for coroutine in spawned:
        coroutine.close()


@pytest.mark.asyncio
async def test_reco_profile_change_and_outbox_roll_back_together(db_session):
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
    customer = await make_customer(db_session)

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(reco_cache, "get_redis", redis_unavailable)

    key = ("reco_cache", "invalidate")
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

        # The worker must observe Redis failure and retry the durable event.
        with pytest.raises(ConnectionError, match="redis unavailable"):
            await handler(db_session, row)
    finally:
        if previous is None:
            OUTBOX_HANDLERS.pop(key, None)
        else:
            OUTBOX_HANDLERS[key] = previous
