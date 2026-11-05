"""数据库级 Leader epoch fencing 测试。"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import database
from app.core.leader_fencing import (
    LeaderFenceLostError,
    acquire_database_epoch,
    activate_leader_epoch,
    deactivate_leader_epoch,
    leader_fence_scope,
)
from app.models.leader import LeaderFencingState


@pytest.fixture
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
    maker = async_sessionmaker(engine, class_=database.AsyncSessionLocal.class_, expire_on_commit=False)
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
async def test_old_leader_epoch_is_rejected_at_database_commit(fencing_db):
    activate_leader_epoch(11, "leader-old")
    async with fencing_db() as db:
        await db.execute(
            text(
                "UPDATE leader_fencing_state SET epoch = 12, owner_id = 'leader-new' "
                "WHERE id = 1"
            )
        )
        await db.commit()

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
