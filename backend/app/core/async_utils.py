"""异步工具：后台任务引用持有，防止 GC 回收 fire-and-forget 的 create_task。

Python asyncio 中 create_task 返回的 Task 对象若无外部引用，可能被 GC 提前回收，
导致任务未完成就被取消。本模块提供统一的 spawn_bg 辅助，将任务加入全局集合，
并在任务结束时自动清理（done 回调 discard）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

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
