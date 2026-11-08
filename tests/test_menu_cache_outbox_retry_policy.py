from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from app.services.menu_cache_service import (
    MENU_CACHE_INVALIDATION_MAX_ATTEMPTS,
    enqueue_menu_cache_invalidation,
)


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
    assert row.max_attempts == MENU_CACHE_INVALIDATION_MAX_ATTEMPTS
    assert row.max_attempts == 2_147_483_647
