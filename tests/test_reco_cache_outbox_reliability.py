from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from app.services import reco_cache
from app.services.outbox_worker import OUTBOX_HANDLERS, _resolve_outbox_handler
from reco_factories import make_customer


@pytest.mark.asyncio
async def test_reco_invalidation_outbox_is_durable_and_unique_per_update(db_session, monkeypatch):
    customer = await make_customer(db_session)
    spawned = []

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    # Keep after-commit best-effort tasks under test control; the DB outbox is the
    # durable recovery path and must not depend on this immediate Redis attempt.
    monkeypatch.setattr(reco_cache, "get_redis", redis_unavailable)
    monkeypatch.setattr(reco_cache, "spawn_bg", lambda coro: spawned.append(coro))

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
    assert all(json.loads(row.payload or "{}") == {"customer_id": customer.id} for row in pending)

    await db_session.commit()

    # The invalidation records survive a Redis outage because they were inserted
    # in the same DB transaction as the profile mutation.
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

        # The worker must observe the Redis failure and mark this durable event
        # retryable; silently swallowing it would lose cache invalidation.
        with pytest.raises(ConnectionError, match="redis unavailable"):
            await handler(db_session, row)
    finally:
        if previous is None:
            OUTBOX_HANDLERS.pop(key, None)
        else:
            OUTBOX_HANDLERS[key] = previous
