"""数据库级 Leader fencing。

每个 Leader 接管时由数据库分配单调递增 epoch。Leader 定时任务的 AsyncSession
在 commit 前锁定 fencing 行并校验 (epoch, owner_id)，锁一直持有到业务事务提交。
因此旧 Leader 的事务与新 epoch 递增在数据库内形成严格先后关系。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LeaderFenceLostError(RuntimeError):
    """当前事务使用的 Leader epoch 已过期，拒绝提交。"""


_current_fence: ContextVar[tuple[int, str] | None] = ContextVar(
    "smart_noodle_leader_fence", default=None
)
_active_epoch: int | None = None
_active_owner: str | None = None


def activate_leader_epoch(epoch: int, owner_id: str) -> None:
    global _active_epoch, _active_owner
    if int(epoch) <= 0 or not owner_id:
        raise ValueError("invalid leader fencing identity")
    _active_epoch = int(epoch)
    _active_owner = str(owner_id)[:64]


def deactivate_leader_epoch(owner_id: str | None = None) -> None:
    global _active_epoch, _active_owner
    if owner_id is not None and _active_owner != owner_id:
        return
    _active_epoch = None
    _active_owner = None


def active_leader_epoch() -> int | None:
    return _active_epoch


@contextmanager
def leader_fence_scope() -> Iterator[tuple[int, str]]:
    """把当前进程已激活的 epoch 绑定到任务上下文（子任务自动继承）。"""
    if _active_epoch is None or _active_owner is None:
        raise LeaderFenceLostError("本进程没有有效的数据库 Leader epoch")
    identity = (_active_epoch, _active_owner)
    token = _current_fence.set(identity)
    try:
        yield identity
    finally:
        _current_fence.reset(token)


async def acquire_database_epoch(owner_id: str) -> int:
    """新 Leader 通过 DB 原子递增 epoch；迁移未执行/DB 异常时必须失败关闭。"""
    from sqlalchemy import update

    from app.core.database import AsyncSessionLocal
    from app.models import LeaderFencingState

    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                update(LeaderFencingState)
                .where(LeaderFencingState.id == 1)
                .values(
                    epoch=LeaderFencingState.epoch + 1,
                    owner_id=str(owner_id)[:64],
                )
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    "leader_fencing_state singleton row missing; apply database migration first"
                )
            epoch = await db.scalar(
                text("SELECT epoch FROM leader_fencing_state WHERE id = 1")
            )
            if epoch is None or int(epoch) <= 0:
                raise RuntimeError("database returned invalid leader fencing epoch")
            value = int(epoch)
    activate_leader_epoch(value, owner_id)
    logger.warning("Database Leader fencing epoch activated epoch=%s owner=%s", value, owner_id)
    return value


async def revoke_database_epoch(owner_id: str) -> bool:
    """Leader 丢锁时主动废止旧 epoch；若新 Leader 已接管则不覆盖其 owner。"""
    from sqlalchemy import update

    from app.core.database import AsyncSessionLocal
    from app.models import LeaderFencingState

    async with AsyncSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                update(LeaderFencingState)
                .where(
                    LeaderFencingState.id == 1,
                    LeaderFencingState.owner_id == str(owner_id)[:64],
                )
                .values(
                    epoch=LeaderFencingState.epoch + 1,
                    owner_id=None,
                )
            )
            return result.rowcount == 1


def _before_commit_fence(session: Session) -> None:
    identity = _current_fence.get()
    if identity is None:
        return
    expected_epoch, expected_owner = identity
    dialect = session.get_bind().dialect.name
    sql = "SELECT epoch, owner_id FROM leader_fencing_state WHERE id = 1"
    if dialect == "mysql":
        sql += " FOR UPDATE"
    row = session.execute(text(sql)).first()
    if row is None:
        raise LeaderFenceLostError("leader fencing row missing; refusing business commit")
    actual_epoch, actual_owner = int(row[0]), row[1]
    if actual_epoch != expected_epoch or actual_owner != expected_owner:
        raise LeaderFenceLostError(
            "leader fencing mismatch: "
            f"expected epoch={expected_epoch} owner={expected_owner}, "
            f"actual epoch={actual_epoch} owner={actual_owner}"
        )


# AsyncSession 的 sync_session 使用 SQLAlchemy Session 事件；before_commit 在 flush/commit
# 前执行，MySQL FOR UPDATE 行锁会一直持有到该业务事务提交或回滚。
event.listen(Session, "before_commit", _before_commit_fence)
