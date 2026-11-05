"""后台 worker 公共工具：实例标识与重试退避。"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import update

from app.core.database import AsyncSessionLocal
from app.core.response import now_local

logger = logging.getLogger(__name__)


def worker_id() -> str:
    """worker 实例标识：主机名:进程号。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def backoff_seconds(attempt_count: int, *, base: int = 2, cap: int = 600) -> int:
    """指数退避：base^attempt 秒，封顶 cap。"""
    exp = max(0, int(attempt_count))
    return min(cap, base**exp)


def claim_token(worker: str | None = None) -> str:
    """每次 DB 行 claim 生成独立 owner token，避免同一进程 reclaim 后旧执行者误更新新 claim。"""
    prefix = (worker or worker_id()).strip()[:31] or "worker"
    return f"{prefix}:{uuid.uuid4().hex[:32]}"


@asynccontextmanager
async def claim_lease_guard(
    model,
    row_id: int,
    token: str,
    *,
    interval_seconds: float = 60.0,
) -> AsyncIterator[None]:
    """为 Outbox/Inbox PROCESSING claim 续租；续租失败或 owner 变化时取消旧执行者。

    使用独立 DB session，绝不与业务 handler 正在使用的 AsyncSession 并发共享。
    """
    owner_task = asyncio.current_task()
    if owner_task is None or not token:
        raise RuntimeError("claim lease requires current task and owner token")
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        interval = max(1.0, float(interval_seconds))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                async with AsyncSessionLocal() as lease_db:
                    result = await lease_db.execute(
                        update(model)
                        .where(
                            model.id == int(row_id),
                            model.status == "PROCESSING",
                            model.locked_by == token,
                        )
                        .values(locked_at=now_local())
                    )
                    await lease_db.commit()
                if int(result.rowcount or 0) != 1:
                    logger.error(
                        "后台任务 claim 租约已丢失 model=%s id=%s，取消旧执行者",
                        getattr(model, "__name__", str(model)),
                        row_id,
                    )
                    owner_task.cancel()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                # 无法证明仍持有 claim 时 fail-closed；不能冒险继续外部副作用。
                logger.exception(
                    "后台任务 claim 租约续期失败 model=%s id=%s，取消旧执行者",
                    getattr(model, "__name__", str(model)),
                    row_id,
                )
                owner_task.cancel()
                return

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        yield
    finally:
        stop.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass



async def rollback_if_claim_lost(db, result, *, model, row_id: int) -> bool:
    """DONE 条件更新未命中唯一 claim owner 时回滚整个业务事务。

    返回 True 表示 claim 已丢失且事务已回滚；调用方必须立即停止，不得提交、
    写幂等完成标记或发送成功通知。rowcount=None 也按无法证明所有权处理。
    """
    if int(getattr(result, "rowcount", 0) or 0) == 1:
        return False
    await db.rollback()
    logger.warning(
        "后台任务完成时 claim 所有权已丢失，已回滚业务事务 model=%s id=%s",
        getattr(model, "__name__", str(model)),
        row_id,
    )
    return True
