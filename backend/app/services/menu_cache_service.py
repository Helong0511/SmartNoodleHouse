"""菜单缓存代际与失效 Outbox。

MySQL 保存唯一可信的持久代际；Redis 版本只是可重建镜像，主从切换或全量丢失后可恢复。
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
    """在调用方业务事务内递增门店菜单代际。"""
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
    """仅在当前生效的时段方案发生变化时，原子递增一次菜单代际。"""
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
    # This helper is used only by the menu read path. Commit the metadata transition
    # here so the following plain SELECT starts a fresh READ COMMITTED snapshot and
    # does not hold a store-row FOR UPDATE lock across the whole menu build.
    await db.commit()
    value = await db.scalar(select(Store.menu_generation).where(Store.id == sid))
    if value is None:
        raise ValueError(f"menu generation store not found: {sid}")
    return int(value)


async def enqueue_menu_cache_invalidation(db: AsyncSession, store_id: int | None) -> None:
    """在同一个数据库事务内递增持久代际并写入缓存失效 Outbox。"""
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

async def invalidate_store_menu_cache(db: AsyncSession, store_id: int | None) -> None:
    """立即清理当前进程缓存，并在业务事务中持久化菜单代际与失效事件。"""
    if not store_id:
        return
    sid = int(store_id)
    from app.services.recommendation_service import invalidate_store_local_caches

    invalidate_store_local_caches(sid)
    await enqueue_menu_cache_invalidation(db, sid)
