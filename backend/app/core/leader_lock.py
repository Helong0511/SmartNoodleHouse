"""多进程 Leader 选举：Redis NX 锁，防止 gunicorn 多 worker 重复跑调度。"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from time import monotonic
from collections.abc import Awaitable, Callable

from app.core.cache_keys import LEADER_KEY
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)
_LEADER_REDIS_ERROR_LOG_INTERVAL_SEC = 30.0
_last_leader_redis_error_log_at = -_LEADER_REDIS_ERROR_LOG_INTERVAL_SEC
_suppressed_leader_redis_error_logs = 0


def _log_leader_redis_error(error: Exception) -> None:
    global _last_leader_redis_error_log_at, _suppressed_leader_redis_error_logs
    now = monotonic()
    if now - _last_leader_redis_error_log_at < _LEADER_REDIS_ERROR_LOG_INTERVAL_SEC:
        _suppressed_leader_redis_error_logs += 1
        return
    suppressed = _suppressed_leader_redis_error_logs
    _suppressed_leader_redis_error_logs = 0
    _last_leader_redis_error_log_at = now
    logger.error("Leader Redis 租约确认/续期异常，已按 fail-closed 停止新任务 suppressed=%s err=%s", suppressed, error)
DEFAULT_TTL = 45
RENEW_INTERVAL = 10

# Lua 原子脚本：避免 GET→DELETE / GET→EXPIRE 的 TOCTOU（A 的 release 误删 B 的锁等）
# compare-and-delete：仅当值=worker_id 时才 DEL
_LUA_COMPARE_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
# compare-and-expire：仅当值=worker_id 时才 EXPIRE（续期）
_LUA_COMPARE_EXPIRE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""

# 本进程是否仍持有 Leader（续期失败时清 False，供调度任务 fence）
_held = False
_worker_id: str | None = None
_on_lost: Callable[[], Awaitable[None] | None] | None = None


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def is_leader_held() -> bool:
    return bool(_held)


def current_leader_worker_id() -> str | None:
    return _worker_id


def set_leader_held(value: bool) -> None:
    global _held
    _held = bool(value)


def set_leader_worker_id(worker_id: str | None) -> None:
    """记录本进程竞选用的 worker_id，供任务 fence 对照 Redis 锁值。"""
    global _worker_id
    _worker_id = worker_id


def set_on_leader_lost(cb: Callable[[], Awaitable[None] | None] | None) -> None:
    """锁丢失时回调（停 scheduler / TTS 等）。"""
    global _on_lost
    _on_lost = cb


async def confirm_leader_owned(*, fail_open: bool = False) -> bool:
    """任务执行前二次确认：内存旗标 + Redis 锁值仍为本 worker。

    缩小「锁 TTL 已过期、另一进程已成为 Leader，但本进程尚未跑到续期循环」
    的双 Leader 窗口。Redis 短暂不可达时默认 fail-closed（跳过本次任务）。
    """
    if not _held or not _worker_id:
        return False
    try:
        r = await get_redis()
        cur = await r.get(LEADER_KEY)
        if cur is None:
            set_leader_held(False)
            return False
        cur_s = cur.decode() if isinstance(cur, (bytes, bytearray)) else str(cur)
        if cur_s != _worker_id:
            set_leader_held(False)
            return False
        return True
    except Exception as e:
        _log_leader_redis_error(e)
        if fail_open:
            return bool(_held)
        # Redis 状态未知时立即关闭本地 Leader 门闩；不能等续租循环第二次失败，
        # 否则摄像头/TTS 等非数据库副作用仍可能继续运行。
        set_leader_held(False)
        return False


async def try_acquire_leader(worker_id: str, *, ttl: int = DEFAULT_TTL) -> bool:
    """尝试成为 leader。已持有则续期。Redis 失败时 fail-closed（不担任 leader）。"""
    set_leader_worker_id(worker_id)
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
            # 原子 compare-and-expire：避免 GET→EXPIRE 之间锁过期被 B 抢占后误续期 B 的锁
            renewed = await r.eval(_LUA_COMPARE_EXPIRE, 1, LEADER_KEY, worker_id, int(ttl))
            if renewed:
                set_leader_held(True)
                return True
            # 续期失败说明锁已被他人抢占
            set_leader_held(False)
            return False
        set_leader_held(False)
        return False
    except Exception as e:
        logger.error("Leader 锁获取失败，降级为本进程不担任 leader: %s", e)
        set_leader_held(False)
        return False


async def renew_leader_lease(worker_id: str, *, ttl: int = DEFAULT_TTL) -> bool:
    """续期专用路径：Redis 网络异常向上抛出，锁不匹配才返回 False。"""
    r = await get_redis()
    renewed = await r.eval(_LUA_COMPARE_EXPIRE, 1, LEADER_KEY, worker_id, int(ttl))
    if renewed:
        set_leader_held(True)
        return True
    set_leader_held(False)
    return False


async def release_leader(worker_id: str) -> None:
    try:
        r = await get_redis()
        # 原子 compare-and-delete：仅当锁仍归本 worker 时才 DEL，
        # 避免 GET→DELETE 之间锁过期被 B 抢占后误删 B 的锁
        await r.eval(_LUA_COMPARE_DELETE, 1, LEADER_KEY, worker_id)
        set_leader_held(False)
    except Exception as e:
        logger.warning("释放 Leader 锁失败: %s", e)
        set_leader_held(False)


async def renew_leader_loop(worker_id: str, stop: asyncio.Event, *, ttl: int = DEFAULT_TTL) -> None:
    """后台续期，直到 stop 置位；锁丢失时触发 on_lost 并退出循环。"""
    consecutive_errors = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=RENEW_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            held = await renew_leader_lease(worker_id, ttl=ttl)
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
            # Redis owner 仍匹配并不足够：数据库 epoch 是业务写入的最终 fencing 真源。
            # 其他进程可能在 Redis 故障转移后取得更新 epoch，必须及时停止旧 Leader。
            from app.core.leader_fencing import confirm_database_epoch

            if not await confirm_database_epoch(worker_id):
                logger.error(
                    "Leader 数据库 fencing epoch 已失效，停止本进程业务 worker（worker_id=%s）",
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
                        logger.exception("数据库 fencing 失效回调失败")
                stop.set()
                break
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            # 任意一次续租异常都立即阻止新 Leader 工作；下一轮若续租恢复，
            # renew_leader_lease 会重新打开门闩。连续两次失败仍会执行完整降级回调。
            set_leader_held(False)
            _log_leader_redis_error(e)
            # 连续两次异常才放弃；10 秒续期间隔确保在 45 秒租约过期前留有安全余量
            if consecutive_errors >= 2:
                logger.error("Leader 锁续期连续失败，降级退出 worker_id=%s", worker_id)
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
