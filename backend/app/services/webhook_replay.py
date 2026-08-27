"""Webhook / 支付回调防重放（Redis + 可选 DB）"""
from __future__ import annotations

import hashlib
import logging

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

REDIS_NS = "snh:webhook:replay:"
DEFAULT_TTL = 86400 * 2  # 48h


async def claim_once(channel: str, receipt_key: str, ttl: int = DEFAULT_TTL) -> bool:
    """
    原子认领一次回调。返回 True 表示首次处理；False 表示重复（应幂等返回成功）。
    """
    key = f"{REDIS_NS}{channel}:{receipt_key}"
    try:
        r = await get_redis()
        ok = await r.set(key, "1", nx=True, ex=ttl)
        return bool(ok)
    except Exception as e:
        logger.error("Redis replay claim 失败，降级放行但记录: %s", e)
        # Redis 不可用时不能静默丢单；由上层 DB unique 兜底
        return True


def make_receipt_key(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]
