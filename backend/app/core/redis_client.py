"""Redis 客户端：缓存加速，不可当作唯一数据源。

约定：
- 业务正确性必须以 MySQL 为准；
- Redis 不可用时应降级（查库 / 放行 / 跳过缓存），而不是让全站不可用；
- Leader 选举等「防双跑」场景可 fail-closed（宁可停调度也不能双写）。
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
import uuid

import redis.asyncio as redis
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_REDIS_DEGRADE_LOG_INTERVAL_SEC = 30.0
_last_redis_degrade_log_at = -_REDIS_DEGRADE_LOG_INTERVAL_SEC
_suppressed_redis_degrade_logs = 0


def _log_redis_degraded(error: Exception) -> None:
    global _last_redis_degrade_log_at, _suppressed_redis_degrade_logs
    now = monotonic()
    if now - _last_redis_degrade_log_at < _REDIS_DEGRADE_LOG_INTERVAL_SEC:
        _suppressed_redis_degrade_logs += 1
        return
    suppressed = _suppressed_redis_degrade_logs
    _suppressed_redis_degrade_logs = 0
    _last_redis_degrade_log_at = now
    logger.warning("Redis 不可用，进入降级: %s（此前抑制重复日志 %s 次）", error, suppressed)

# 只允许锁持有者释放自己的锁；禁止无条件 DEL（锁过期并被接管后会误删新持有者）。
_LUA_RELEASE_OWNED_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def release_owned_lock(r, key: str, token: str) -> bool:
    """原子比较 token 并释放锁；token 不匹配时绝不删除其他进程的锁。"""
    if not token:
        return False
    try:
        return bool(await r.eval(_LUA_RELEASE_OWNED_LOCK, 1, key, token))
    except Exception:
        logger.debug("Redis owner-checked lock release failed: key=%s", key, exc_info=True)
        return False

_redis: redis.Redis | None = None
_redis_pool: redis.BlockingConnectionPool | None = None
_lock = asyncio.Lock()

# 池耗尽时有限等待后快速失败，避免默认 ConnectionPool 立即拒绝造成突发流量抖动，
# 也避免无限排队拖垮 API。业务层仍按各自策略 fail-open / fail-closed。
_REDIS_POOL_WAIT_TIMEOUT_SEC = 0.75


async def get_redis() -> redis.Redis:
    global _redis, _redis_pool
    if _redis is None:
        # 并发首调仅创建一个实例，避免被覆盖的实例连接泄漏。
        # BlockingConnectionPool 在达到 max_connections 时最多等待 750ms，
        # 超时后抛 ConnectionError；调用方已有降级/关键路径 fail-closed 策略。
        async with _lock:
            if _redis is None:
                settings = get_settings()
                pool = redis.BlockingConnectionPool.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    max_connections=settings.redis_max_connections,
                    timeout=_REDIS_POOL_WAIT_TIMEOUT_SEC,
                    socket_connect_timeout=2.0,
                    socket_timeout=3.0,
                    health_check_interval=30,
                )
                _redis_pool = pool
                _redis = redis.Redis(connection_pool=pool)
    return _redis


async def close_redis() -> None:
    """关闭当前进程 Redis 连接池；应用 shutdown/reload 时调用。"""
    global _redis, _redis_pool
    async with _lock:
        client = _redis
        pool = _redis_pool
        _redis = None
        _redis_pool = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.debug("关闭 Redis 客户端失败", exc_info=True)
    # 显式关闭 pool：避免 Redis 客户端构造方式/版本的 auto-close 默认值差异。
    if pool is not None:
        try:
            await pool.disconnect(inuse_connections=True)
        except Exception:
            logger.debug("关闭 Redis 连接池失败", exc_info=True)


async def try_get_redis() -> redis.Redis | None:
    """获取 Redis；失败返回 None（供降级路径使用）。"""
    try:
        r = await get_redis()
        # 轻量探活：避免拿到已断开的假连接后才在业务里爆炸
        await r.ping()
        return r
    except Exception as e:
        _log_redis_degraded(e)
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


# Redis 重建锁续租：缓存计算可能超过短租约 TTL，必须按 owner token 原子续期。
_LUA_RENEW_OWNED_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def start_redis_lock_heartbeat(
    r,
    key: str,
    token: str,
    *,
    ttl: int,
    label: str = "redis-lock",
) -> asyncio.Task:
    """启动 owner-token 心跳。租约丢失后停止续期；调用方发布前仍必须做 owner fence。

    Redis 短暂故障时继续尝试续期，但若租约已被接管，Lua owner 校验会终止旧心跳。
    """
    lease_ttl = max(3, int(ttl))
    interval = max(1.0, min(10.0, lease_ttl / 3.0))
    owner_task = asyncio.current_task()

    async def _run() -> None:
        while True:
            await asyncio.sleep(interval)
            # 请求/构建任务异常退出或被取消时，不得由心跳永久续租孤儿锁。
            if owner_task is None or owner_task.done():
                logger.warning("Redis 锁持有者任务已结束，停止心跳 label=%s key=%s", label, key)
                return
            try:
                renewed = await r.eval(
                    _LUA_RENEW_OWNED_LOCK, 1, key, token, lease_ttl
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Redis 锁续租暂时失败 label=%s key=%s", label, key, exc_info=True)
                continue
            if not renewed:
                logger.warning("Redis 锁租约已丢失，停止旧持有者续租 label=%s key=%s", label, key)
                return

    return asyncio.create_task(_run(), name=f"redis-lock-heartbeat:{label}")


async def stop_redis_lock_heartbeat(task: asyncio.Task | None) -> None:
    """停止并等待锁心跳，避免释放后留下后台任务。"""
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("Redis 锁心跳退出异常", exc_info=True)
