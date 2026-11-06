"""菜单缓存失效 Outbox handler。

库存变更与订单事务同提交；只有事务提交后 worker 才递增菜单版本并清缓存。
"""
from __future__ import annotations

import json
import logging

from app.services.outbox_worker import register_outbox_handler

logger = logging.getLogger(__name__)


@register_outbox_handler("menu_cache", "invalidate")
async def menu_cache_invalidate_handler(db, row) -> None:
    payload = json.loads(row.payload) if row.payload else {}
    store_id = payload.get("store_id") or row.aggregate_id
    if not store_id:
        raise ValueError(f"菜单缓存失效事件缺少 store_id: outbox_id={row.id}")

    from app.services.recommendation_service import invalidate_store_caches

    if not await invalidate_store_caches(int(store_id)):
        raise RuntimeError(f"菜单缓存失效失败，等待 outbox 重试: store_id={store_id}")
