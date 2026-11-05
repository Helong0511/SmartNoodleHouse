"""数据库级 Leader epoch fencing 测试。"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import database
from app.core.leader_fencing import (
    LeaderFenceLostError,
    acquire_database_epoch,
    activate_leader_epoch,
    deactivate_leader_epoch,
    leader_fence_scope,
)
from app.models.leader import LeaderFencingState


@pytest_asyncio.fixture
async def fencing_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(LeaderFencingState.__table__.create)
        await conn.execute(
            text(
                "CREATE TABLE fenced_test_rows "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO leader_fencing_state (id, epoch, owner_id) "
                "VALUES (1, 0, NULL)"
            )
        )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", maker)
    yield maker
    deactivate_leader_epoch()
    await engine.dispose()


@pytest.mark.asyncio
async def test_database_epoch_is_monotonic_and_owner_bound(fencing_db):
    first = await acquire_database_epoch("leader-A")
    second = await acquire_database_epoch("leader-B")

    assert first == 1
    assert second == 2

    async with fencing_db() as db:
        row = (
            await db.execute(
                text("SELECT epoch, owner_id FROM leader_fencing_state WHERE id = 1")
            )
        ).one()
    assert int(row.epoch) == 2
    assert row.owner_id == "leader-B"


@pytest.mark.asyncio
async def test_current_epoch_can_commit_business_transaction(fencing_db):
    # 测试身份必须与数据库权威行一致，否则正确的 fencing 应拒绝提交。
    async with fencing_db() as db:
        await db.execute(
            text(
                "UPDATE leader_fencing_state "
                "SET epoch = 7, owner_id = 'leader-current' WHERE id = 1"
            )
        )
        await db.commit()

    activate_leader_epoch(7, "leader-current")
    async with fencing_db() as db:
        with leader_fence_scope():
            await db.execute(
                text("INSERT INTO fenced_test_rows (value) VALUES ('current')")
            )
            await db.commit()

    async with fencing_db() as db:
        count = await db.scalar(text("SELECT COUNT(*) FROM fenced_test_rows"))
    assert count == 1


@pytest.mark.asyncio
async def test_old_leader_epoch_is_rejected_and_business_write_is_rolled_back(fencing_db):
    async with fencing_db() as db:
        await db.execute(
            text(
                "UPDATE leader_fencing_state "
                "SET epoch = 12, owner_id = 'leader-new' WHERE id = 1"
            )
        )
        await db.commit()

    activate_leader_epoch(11, "leader-old")
    async with fencing_db() as stale_db:
        with leader_fence_scope():
            await stale_db.execute(
                text("INSERT INTO fenced_test_rows (value) VALUES ('stale')")
            )
            with pytest.raises(LeaderFenceLostError, match="fencing mismatch"):
                await stale_db.commit()
        await stale_db.rollback()

    async with fencing_db() as db:
        count = await db.scalar(text("SELECT COUNT(*) FROM fenced_test_rows"))
    assert count == 0


@pytest.mark.asyncio
async def test_non_leader_request_without_fence_scope_can_commit(fencing_db):
    # 普通 API 请求不应因为同一进程当前存在 Leader 而被错误绑定到 Leader epoch。
    async with fencing_db() as db:
        await db.execute(
            text("INSERT INTO fenced_test_rows (value) VALUES ('api-request')")
        )
        await db.commit()

    async with fencing_db() as db:
        count = await db.scalar(text("SELECT COUNT(*) FROM fenced_test_rows"))
    assert count == 1


@pytest.mark.asyncio
async def test_fenced_commit_fails_closed_when_singleton_row_is_missing(fencing_db):
    async with fencing_db() as db:
        await db.execute(text("DELETE FROM leader_fencing_state WHERE id = 1"))
        await db.commit()

    activate_leader_epoch(3, "leader-with-missing-row")
    async with fencing_db() as db:
        with leader_fence_scope():
            await db.execute(
                text("INSERT INTO fenced_test_rows (value) VALUES ('must-not-commit')")
            )
            with pytest.raises(LeaderFenceLostError, match="row missing"):
                await db.commit()
        await db.rollback()

    async with fencing_db() as db:
        count = await db.scalar(text("SELECT COUNT(*) FROM fenced_test_rows"))
    assert count == 0



@pytest.mark.asyncio
async def test_revoke_epoch_only_revokes_matching_owner(fencing_db):
    from app.core.leader_fencing import confirm_database_epoch, revoke_database_epoch

    async with fencing_db() as db:
        await db.execute(
            text(
                "UPDATE leader_fencing_state SET epoch = 21, owner_id = 'leader-A' "
                "WHERE id = 1"
            )
        )
        await db.commit()
    activate_leader_epoch(21, "leader-A")

    assert await revoke_database_epoch("leader-B") is False
    assert await confirm_database_epoch("leader-A") is True
    assert await revoke_database_epoch("leader-A") is True
    assert await confirm_database_epoch("leader-A") is False

    async with fencing_db() as db:
        row = (
            await db.execute(
                text("SELECT epoch, owner_id FROM leader_fencing_state WHERE id = 1")
            )
        ).one()
    assert int(row.epoch) == 22
    assert row.owner_id is None



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_old_leader_commit_uses_current_read_and_is_rejected(db_session):
    """MySQL REPEATABLE READ 下，before_commit 的 FOR UPDATE 必须读到最新 epoch。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8")

    maker = async_sessionmaker(
        db_session.get_bind(), class_=AsyncSession, expire_on_commit=False
    )
    old_owner = "mysql-fence-old"
    new_owner = "mysql-fence-new"
    probe_value = "fence-probe-" + __import__("uuid").uuid4().hex

    deactivate_leader_epoch()
    try:
        async with maker() as setup:
            await setup.execute(
                text(
                    "INSERT INTO leader_fencing_state (id, epoch, owner_id) "
                    "VALUES (1, 1001, :owner) "
                    "ON DUPLICATE KEY UPDATE epoch = VALUES(epoch), owner_id = VALUES(owner_id)"
                ),
                {"owner": old_owner},
            )
            await setup.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS leader_fence_ci_probe "
                    "(value VARCHAR(100) PRIMARY KEY)"
                )
            )
            await setup.commit()

        old_tx = maker()
        try:
            # 先在旧事务中写入业务数据，建立事务上下文，但暂不提交。
            await old_tx.execute(
                text("INSERT INTO leader_fence_ci_probe (value) VALUES (:value)"),
                {"value": probe_value},
            )

            # 新 Leader 在另一个事务中推进 epoch 并提交。
            async with maker() as new_tx:
                await new_tx.execute(
                    text(
                        "UPDATE leader_fencing_state "
                        "SET epoch = 1002, owner_id = :owner WHERE id = 1"
                    ),
                    {"owner": new_owner},
                )
                await new_tx.commit()

            activate_leader_epoch(1001, old_owner)
            with leader_fence_scope():
                with pytest.raises(LeaderFenceLostError, match="fencing mismatch"):
                    await old_tx.commit()
            await old_tx.rollback()
        finally:
            await old_tx.close()

        async with maker() as verify:
            count = await verify.scalar(
                text(
                    "SELECT COUNT(*) FROM leader_fence_ci_probe WHERE value = :value"
                ),
                {"value": probe_value},
            )
            assert count == 0
    finally:
        deactivate_leader_epoch()
        async with maker() as cleanup:
            await cleanup.execute(text("DROP TABLE IF EXISTS leader_fence_ci_probe"))
            await cleanup.commit()
