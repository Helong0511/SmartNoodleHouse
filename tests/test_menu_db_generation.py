from __future__ import annotations

import pytest

from app.models import Store
from app.services.menu_cache_service import (
    advance_store_menu_generation,
    sync_store_menu_schedule_generation,
)


@pytest.mark.asyncio
async def test_mysql_generation_is_authoritative_and_schedule_transition_is_idempotent(db_session):
    store = Store(store_no="GEN-TEST-01", name="代际测试门店", status=1)
    db_session.add(store)
    await db_session.flush()
    store_id = int(store.id)

    initial = int(store.menu_generation)
    assert initial >= 1_000_000_000

    # Initial no-schedule marker is already -1; same state must not bump generation.
    assert await sync_store_menu_schedule_generation(db_session, store_id, None) == initial

    # Concurrent requests observing the same schedule transition must only bump once.
    first = await sync_store_menu_schedule_generation(db_session, store_id, 17)
    second = await sync_store_menu_schedule_generation(db_session, store_id, 17)
    assert first == initial + 1
    assert second == first

    # A subsequent business mutation advances the same durable sequence.
    changed = await advance_store_menu_generation(db_session, store_id)
    assert changed == first + 1

    await db_session.flush()
    refreshed = await db_session.get(Store, store_id)
    assert refreshed is not None
    assert refreshed.menu_generation == changed
    assert refreshed.menu_schedule_id == 17
