"""后台 worker 公共工具：实例标识与重试退避。"""
from __future__ import annotations

import os
import socket


def worker_id() -> str:
    """worker 实例标识：主机名:进程号。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def backoff_seconds(attempt_count: int, *, base: int = 2, cap: int = 600) -> int:
    """指数退避：base^attempt 秒，封顶 cap。"""
    exp = max(0, int(attempt_count))
    return min(cap, base**exp)
