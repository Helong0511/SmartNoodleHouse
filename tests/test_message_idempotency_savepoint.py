"""Regression tests for transaction-safe message idempotency claims."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.domains.messaging import idempotency
from app.models import ProcessedMessage


@pytest.mark.asyncio
async def test_unique_race_isolated_inside_savepoint_without_poisoning_outer_transaction(
    db_session, monkeypatch
):
    """Simulate another worker winning the UNIQUE insert after our preflight SELECT.

    The initial SELECT is forced to miss the concurrent row. The actual INSERT then
    collides with the already committed UNIQUE key. The SAVEPOINT must contain that
    IntegrityError so the caller's surrounding transaction remains usable.
    """
    db_session.add(
        ProcessedMessage(
            scope="outbox",
            message_id="same-event",
            meta_json=None,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(
        idempotency, "_redis_seen", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(idempotency, "_redis_mark", AsyncMock())
    # Model the race window: another worker inserted after our preflight check.
    original_scalar = db_session.scalar
    monkeypatch.setattr(db_session, "scalar", AsyncMock(return_value=None))

    acquired = await idempotency.try_acquire(
        db_session,
        scope="outbox",
        message_id="same-event",
        mark_redis=False,
    )

    assert acquired is False
    monkeypatch.setattr(db_session, "scalar", original_scalar)

    # A failed duplicate insert must not leave the outer Session in PendingRollback.
    existing_count = await db_session.scalar(
        select(func.count(ProcessedMessage.id)).where(
            ProcessedMessage.scope == "outbox",
            ProcessedMessage.message_id == "same-event",
        )
    )
    assert existing_count == 1

    db_session.add(
        ProcessedMessage(
            scope="outbox",
            message_id="different-event",
            meta_json=None,
        )
    )
    await db_session.commit()

    total_count = await db_session.scalar(
        select(func.count(ProcessedMessage.id))
    )
    assert total_count == 2
