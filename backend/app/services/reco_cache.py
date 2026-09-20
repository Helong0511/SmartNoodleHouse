"""推荐结果 Redis 缓存层（Task 8）。

- 仅缓存接口响应 payload（瘦身字段后的 dict），TTL ≤ 120s；
- Redis 不可用时所有操作静默降级，不影响推荐主流程；
- 画像更新 / 下单 / 评价后调用 invalidate_customer 主动失效。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.core.cache_keys import RECO_TTL_SECONDS, reco_customer_pattern, reco_key
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)


async def get_cached_reco(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> dict[str, Any] | None:
    try:
        r = await get_redis()
        raw = await r.get(reco_key(customer_id, store_id, scene, bucket, fingerprint))
        if raw:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                payload["cache_hit"] = True
                return payload
    except Exception:  # noqa: BLE001 - 缓存不可用必须降级
        logger.debug("推荐缓存读取失败，降级直算", exc_info=True)
    return None


async def set_cached_reco(
    customer_id: int | None,
    store_id: int,
    scene: str,
    bucket: str,
    payload: dict[str, Any],
    fingerprint: str = "",
) -> None:
    try:
        r = await get_redis()
        body = dict(payload)
        body["cache_hit"] = False
        await r.set(
            reco_key(customer_id, store_id, scene, bucket, fingerprint),
            json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            ex=RECO_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存写入失败，忽略", exc_info=True)


async def invalidate_customer(customer_id: int) -> None:
    """失效某顾客全部场景/桶位的推荐结果。"""
    if not customer_id:
        return
    try:
        r = await get_redis()
        pattern = reco_customer_pattern(customer_id)
        keys = []
        async for k in r.scan_iter(match=pattern, count=200):
            keys.append(k)
        if keys:
            await r.delete(*keys)
    except Exception:  # noqa: BLE001
        logger.debug("推荐缓存失效失败(customer=%s)，等待 TTL", customer_id, exc_info=True)
