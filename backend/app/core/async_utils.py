"""异步工具：后台任务引用持有，防止 GC 回收 fire-and-forget 的 create_task。

Python asyncio 中 create_task 返回的 Task 对象若无外部引用，可能被 GC 提前回收，
导致任务未完成就被取消。本模块提供统一的 spawn_bg 辅助，将任务加入全局集合，
并在任务结束时自动清理（done 回调 discard）。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable

logger = logging.getLogger(__name__)

# 全局后台任务引用集合
_BG_TASKS: set[asyncio.Task] = set()


def spawn_bg(coro_or_task: Awaitable | asyncio.Task) -> None:
    """启动或登记后台异步任务，持有引用防止 GC。

    Args:
        coro_or_task: 协程对象或已创建的 Task。协程会被 create_task，Task 直接登记。
    """
    try:
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        else:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro_or_task)
    except RuntimeError:
        # 无事件循环（同步测试环境等）：关闭未调度协程，避免 ResourceWarning
        if not isinstance(coro_or_task, asyncio.Task):
            close = getattr(coro_or_task, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


# Redis 不可用或跨进程锁竞争时的本进程 single-flight 兜底。
# ref-count 确保并发等待者全部退出后回收锁对象，避免动态 key 导致内存泄漏。
_SINGLEFLIGHT_GUARD = threading.Lock()
_SINGLEFLIGHT_LOCKS: dict[str, tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def local_singleflight(key: str) -> AsyncIterator[None]:
    """同一进程同一业务 key 同时只允许一个重建者；不替代跨进程 Redis 锁。"""
    normalized = str(key or "").strip()
    if not normalized:
        raise ValueError("singleflight key is required")
    with _SINGLEFLIGHT_GUARD:
        entry = _SINGLEFLIGHT_LOCKS.get(normalized)
        if entry is None:
            lock, users = asyncio.Lock(), 1
        else:
            lock, users = entry[0], entry[1] + 1
        _SINGLEFLIGHT_LOCKS[normalized] = (lock, users)

    acquired = False
    try:
        await lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()
        with _SINGLEFLIGHT_GUARD:
            current = _SINGLEFLIGHT_LOCKS.get(normalized)
            if current is not None and current[0] is lock:
                remaining = current[1] - 1
                if remaining <= 0:
                    _SINGLEFLIGHT_LOCKS.pop(normalized, None)
                else:
                    _SINGLEFLIGHT_LOCKS[normalized] = (lock, remaining)
