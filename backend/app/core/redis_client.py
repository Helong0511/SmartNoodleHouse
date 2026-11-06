"""Redis 客户端：缓存加速，不可当作唯一数据源。

约定：
- 业务正确性必须以 MySQL 为准；
- Redis 不可用时应降级（查库 / 放行 / 跳过缓存），而不是让全站不可用；
- Leader 选举等「防双跑」场景可 fail-closed（宁可停调度也不能双写）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import redis.asyncio as redis
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis: redis.Redis | None = None
_lock = asyncio.Lock()


async def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        # 并发首调仅创建一个实例，避免被覆盖的实例连接泄漏
        async with _lock:
            if _redis is None:
                _redis = redis.from_url(
                    get_settings().redis_url,
                    decode_responses=True,
                    max_connections=get_settings().redis_max_connections,
                )
    return _redis


async def try_get_redis() -> redis.Redis | None:
    """获取 Redis；失败返回 None（供降级路径使用）。"""
    try:
        r = await get_redis()
        # 轻量探活：避免拿到已断开的假连接后才在业务里爆炸
        await r.ping()
        return r
    except Exception as e:
        logger.warning("Redis 不可用，进入降级: %s", e)
        return None


async def delete_with_retry(r, *keys: str, retries: int = 2, delay: float = 0.05) -> int:
    """删除缓存键，仅在 Redis 命令异常时重试。

    DEL 返回 0 是成功结果（键不存在或已被其他实例删除），不能当作失败重试；
    尤其逐键删除 Redis Cluster 缓存时，避免对大量不存在的键产生无意义延迟。
    """
    if not keys:
        return 0
    for attempt in range(retries + 1):
        try:
            return int(await r.delete(*keys) or 0)
        except Exception:
            if attempt >= retries:
                logger.warning("Redis DEL 重试耗尽（%d 个 key）", len(keys), exc_info=True)
                return 0
            await asyncio.sleep(delay)
    return 0


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


async def close_redis() -> None:
    """关闭当前 Redis 客户端连接池；可重复调用。"""
    global _redis
    async with _lock:
        client, _redis = _redis, None
    if client is None:
        return
    try:
        # redis-py 5.x 的异步客户端使用 aclose() 释放连接池。
        await client.aclose()
    except Exception:
        # shutdown 阶段尽力释放；不能因 Redis 关闭异常阻断应用退出。
        logger.exception("关闭 Redis 客户端失败")
