"""多进程 Leader 选举：Redis NX 锁，防止 gunicorn 多 worker 重复跑调度。"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

LEADER_KEY = "snh:process:leader"
DEFAULT_TTL = 45
RENEW_INTERVAL = 15


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


async def try_acquire_leader(worker_id: str, *, ttl: int = DEFAULT_TTL) -> bool:
    """尝试成为 leader。已持有则续期。Redis 失败时 fail-closed（不担任 leader）。"""
    try:
        r = await get_redis()
        ok = await r.set(LEADER_KEY, worker_id, nx=True, ex=ttl)
        if ok:
            return True
        cur = await r.get(LEADER_KEY)
        if cur is None:
            return bool(await r.set(LEADER_KEY, worker_id, nx=True, ex=ttl))
        cur_s = cur.decode() if isinstance(cur, (bytes, bytearray)) else str(cur)
        if cur_s == worker_id:
            await r.expire(LEADER_KEY, ttl)
            return True
        return False
    except Exception as e:
        logger.error("Leader 锁获取失败，降级为本进程不担任 leader: %s", e)
        return False


async def release_leader(worker_id: str) -> None:
    try:
        r = await get_redis()
        cur = await r.get(LEADER_KEY)
        if cur is None:
            return
        cur_s = cur.decode() if isinstance(cur, (bytes, bytearray)) else str(cur)
        if cur_s == worker_id:
            await r.delete(LEADER_KEY)
    except Exception as e:
        logger.warning("释放 Leader 锁失败: %s", e)


async def renew_leader_loop(worker_id: str, stop: asyncio.Event, *, ttl: int = DEFAULT_TTL) -> None:
    """后台续期，直到 stop 置位。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=RENEW_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            held = await try_acquire_leader(worker_id, ttl=ttl)
            if not held:
                logger.error("Leader 锁丢失，本进程调度可能与其它实例冲突，请检查 Redis/进程角色")
        except Exception as e:
            logger.warning("Leader 锁续期异常: %s", e)
