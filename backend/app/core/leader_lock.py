"""多进程 Leader 选举：Redis NX 锁，防止 gunicorn 多 worker 重复跑调度。"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

LEADER_KEY = "snh:process:leader"
DEFAULT_TTL = 45
RENEW_INTERVAL = 15

# 本进程是否仍持有 Leader（续期失败时清 False，供调度任务 fence）
_held = False
_on_lost: Callable[[], Awaitable[None] | None] | None = None


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def is_leader_held() -> bool:
    return bool(_held)


def set_leader_held(value: bool) -> None:
    global _held
    _held = bool(value)


def set_on_leader_lost(cb: Callable[[], Awaitable[None] | None] | None) -> None:
    """锁丢失时回调（停 scheduler / TTS 等）。"""
    global _on_lost
    _on_lost = cb


async def try_acquire_leader(worker_id: str, *, ttl: int = DEFAULT_TTL) -> bool:
    """尝试成为 leader。已持有则续期。Redis 失败时 fail-closed（不担任 leader）。"""
    try:
        r = await get_redis()
        ok = await r.set(LEADER_KEY, worker_id, nx=True, ex=ttl)
        if ok:
            set_leader_held(True)
            return True
        cur = await r.get(LEADER_KEY)
        if cur is None:
            ok2 = bool(await r.set(LEADER_KEY, worker_id, nx=True, ex=ttl))
            set_leader_held(ok2)
            return ok2
        cur_s = cur.decode() if isinstance(cur, (bytes, bytearray)) else str(cur)
        if cur_s == worker_id:
            await r.expire(LEADER_KEY, ttl)
            set_leader_held(True)
            return True
        set_leader_held(False)
        return False
    except Exception as e:
        logger.error("Leader 锁获取失败，降级为本进程不担任 leader: %s", e)
        set_leader_held(False)
        return False


async def release_leader(worker_id: str) -> None:
    try:
        r = await get_redis()
        cur = await r.get(LEADER_KEY)
        if cur is None:
            set_leader_held(False)
            return
        cur_s = cur.decode() if isinstance(cur, (bytes, bytearray)) else str(cur)
        if cur_s == worker_id:
            await r.delete(LEADER_KEY)
        set_leader_held(False)
    except Exception as e:
        logger.warning("释放 Leader 锁失败: %s", e)
        set_leader_held(False)


async def renew_leader_loop(worker_id: str, stop: asyncio.Event, *, ttl: int = DEFAULT_TTL) -> None:
    """后台续期，直到 stop 置位；锁丢失时触发 on_lost 并退出循环。"""
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
                logger.error(
                    "Leader 锁丢失，停止本进程调度任务以防双 Leader（worker_id=%s）",
                    worker_id,
                )
                set_leader_held(False)
                cb = _on_lost
                if cb is not None:
                    try:
                        maybe = cb()
                        if asyncio.iscoroutine(maybe) or isinstance(maybe, Awaitable):
                            await maybe  # type: ignore[arg-type]
                    except Exception:
                        logger.exception("Leader 丢失回调失败")
                stop.set()
                break
        except Exception as e:
            logger.warning("Leader 锁续期异常: %s", e)
