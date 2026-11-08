"""事务型菜单缓存失效事件入队。"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.outbox_worker import enqueue_outbox


# Redis outage/OOM is transient infrastructure failure, not a reason to permanently
# abandon menu invalidation. Outbox max_attempts is a signed MySQL INT; this value
# makes retries effectively unbounded while preserving the worker's capped backoff.
MENU_CACHE_INVALIDATION_MAX_ATTEMPTS = 2_147_483_647


async def enqueue_menu_cache_invalidation(db: AsyncSession, store_id: int | None) -> None:
    """在业务事务内写入失效事件；Redis 失效由提交后的 Outbox worker 执行。"""
    if not store_id:
        return
    # 每次变更使用独立事件 ID，避免后续同门店变更被历史幂等记录跳过。
    event_id = uuid.uuid4().int & ((1 << 63) - 1)
    await enqueue_outbox(
        db,
        aggregate_type="menu_cache",
        aggregate_id=event_id,
        event_kind="invalidate",
        payload={"store_id": int(store_id)},
        max_attempts=MENU_CACHE_INVALIDATION_MAX_ATTEMPTS,
    )
