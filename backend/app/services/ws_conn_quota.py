"""WS 连接全局配额（Redis），多 Worker 共享上限。"""
from __future__ import annotations

import logging

from app.core.cache_keys import WS_CONN_IP_PREFIX, WS_CONN_USER_PREFIX

logger = logging.getLogger(__name__)

WS_IP_KEY = WS_CONN_IP_PREFIX
WS_USER_KEY = WS_CONN_USER_PREFIX
# P1-7：TTL 须显著大于 WS 空闲超时(90s)，否则心跳连接在 5min 后配额失效。
# touch() 会在每次心跳时续期，崩溃时靠 TTL 回收。
WS_COUNT_TTL = 600

# 原子 DECR + 归零删除：避免 DECR 与 DELETE 两次往返之间被新连接 INCR，
# 导致旧连接的 DELETE 误删新连接的有效计数（配额被低估）。
_LUA_DECR_DELETE = """
local n = redis.call('decr', KEYS[1])
if n <= 0 then
    redis.call('del', KEYS[1])
end
return n
"""


async def try_reserve(*, ip: str, user_key: str | None, max_ip: int, max_user: int) -> str | None:
    """尝试占用一个全局名额；返回拒绝原因或 None。"""
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        ip_k = f"{WS_IP_KEY}{ip or 'unknown'}"
        pipe = r.pipeline()
        pipe.incr(ip_k)
        pipe.expire(ip_k, WS_COUNT_TTL)
        if user_key:
            uk = f"{WS_USER_KEY}{user_key}"
            pipe.incr(uk)
            pipe.expire(uk, WS_COUNT_TTL)
        results = await pipe.execute()
        ip_n = int(results[0] or 0)
        if ip_n > max_ip:
            await r.decr(ip_k)
            return "同一 IP 连接数超限"
        if user_key:
            user_n = int(results[2] or 0)
            if user_n > max_user:
                await r.decr(ip_k)
                await r.decr(f"{WS_USER_KEY}{user_key}")
                return "同一账号连接数超限"
        return None
    except Exception as e:
        logger.warning("WS 全局连接计数不可用，降级为本进程限额: %s", e)
        return None  # 调用方继续用进程内限额


async def release(*, ip: str, user_key: str | None) -> None:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        ip_k = f"{WS_IP_KEY}{ip or 'unknown'}"
        await r.eval(_LUA_DECR_DELETE, 1, ip_k)
        if user_key:
            uk = f"{WS_USER_KEY}{user_key}"
            await r.eval(_LUA_DECR_DELETE, 1, uk)
    except Exception as e:
        logger.warning("WS 连接计数释放失败（将由 TTL 回收）: %s", e)


async def touch(*, ip: str, user_key: str | None) -> None:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.expire(f"{WS_IP_KEY}{ip or 'unknown'}", WS_COUNT_TTL)
        if user_key:
            await r.expire(f"{WS_USER_KEY}{user_key}", WS_COUNT_TTL)
    except Exception:
        pass
