"""Transactional menu generation updates and cache invalidation Outbox events.

MySQL owns the durable generation. Redis versions are only mirrors and can be
reconstructed after failover or a complete Redis data loss.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store
from app.services.outbox_policy import CACHE_INVALIDATION_MAX_ATTEMPTS
from app.services.outbox_worker import enqueue_outbox


async def _read_generation(db: AsyncSession, store_id: int) -> int:
    value = await db.scalar(select(Store.menu_generation).where(Store.id == int(store_id)).with_for_update())
    if value is None:
        raise ValueError(f"menu generation store not found: {store_id}")
    return int(value)


async def advance_store_menu_generation(db: AsyncSession, store_id: int) -> int:
    """Increment generation in the caller's business transaction."""
    sid = int(store_id)
    result = await db.execute(
        update(Store)
        .where(Store.id == sid)
        .values(menu_generation=Store.menu_generation + 1)
    )
    if result.rowcount != 1:
        raise ValueError(f"menu generation store not found: {sid}")
    return await _read_generation(db, sid)


async def sync_store_menu_schedule_generation(
    db: AsyncSession, store_id: int, active_schedule_id: int | None
) -> int:
    """Atomically advance generation exactly once when the active schedule changes."""
    sid = int(store_id)
    schedule_token = int(active_schedule_id) if active_schedule_id is not None else -1
    await db.execute(
        update(Store)
        .where(Store.id == sid, Store.menu_schedule_id != schedule_token)
        .values(
            menu_generation=Store.menu_generation + 1,
            menu_schedule_id=schedule_token,
        )
    )
    return await _read_generation(db, sid)


async def enqueue_menu_cache_invalidation(db: AsyncSession, store_id: int | None) -> None:
    """Advance durable generation and enqueue invalidation in the same DB transaction."""
    if not store_id:
        return
    sid = int(store_id)
    generation = await advance_store_menu_generation(db, sid)
    # A unique event id prevents a later store update being swallowed by an older event.
    event_id = uuid.uuid4().int & ((1 << 63) - 1)
    await enqueue_outbox(
        db,
        aggregate_type="menu_cache",
        aggregate_id=event_id,
        event_kind="invalidate",
        payload={"store_id": sid, "generation": generation},
        max_attempts=CACHE_INVALIDATION_MAX_ATTEMPTS,
    )
