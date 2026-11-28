"""Regression tests for reconciliation and archive store-level authorization."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from httpx import ASGITransport, AsyncClient

from app.models import ArchivedOrderSnapshot, PaymentReconcileBatch, PaymentReconcileDiff


@pytest.mark.asyncio
async def test_store_admin_cannot_read_other_store_reconciliation_or_archive(db_session):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = SimpleNamespace(
        id=99101,
        role_id=2,
        role=SimpleNamespace(code="STORE_ADMIN"),
        store_id=101,
        status=1,
        token_version=0,
    )
    other_batch = PaymentReconcileBatch(
        channel="WECHAT",
        bill_date=date(2026, 11, 5),
        scope_store_id=202,
        status="DONE",
    )
    hq_batch = PaymentReconcileBatch(
        channel="ALIPAY",
        bill_date=date(2026, 11, 5),
        scope_store_id=0,
        status="DONE",
    )
    db_session.add_all([other_batch, hq_batch])
    await db_session.flush()
    db_session.add(
        PaymentReconcileDiff(
            batch_id=other_batch.id,
            diff_type="LONG",
            channel_trade_no="OTHER-STORE-TRADE",
        )
    )
    db_session.add(
        ArchivedOrderSnapshot(
            order_id=99202,
            store_id=202,
            order_no="ARCHIVE-OTHER-STORE",
            status="COMPLETED",
            payment_status="PAID",
            original_created_at=datetime(2026, 10, 1),
            payload={"order": {"store_id": 202}},
        )
    )
    await db_session.commit()

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        permissions = {
            "reconcile:read",
            "reconcile:manage",
            "archive:manage",
            "order:read",
        }
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=permissions)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                batches = await client.get(
                    "/api/reconcile/payments/batches", params={"store_id": 202}
                )
                assert batches.status_code == 200, batches.text
                assert batches.json()["data"]["total"] == 0

                diffs = await client.get(
                    f"/api/reconcile/payments/batches/{other_batch.id}/diffs"
                )
                assert diffs.status_code == 404, diffs.text

                archived = await client.get(
                    "/api/reconcile/orders/archived", params={"store_id": 202}
                )
                assert archived.status_code == 200, archived.text
                assert archived.json()["data"]["total"] == 0

                archived_detail = await client.get(
                    "/api/reconcile/orders/archived/ARCHIVE-OTHER-STORE"
                )
                assert archived_detail.status_code == 404, archived_detail.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unbound_store_admin_cannot_use_reconcile_or_archive_lists(db_session):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = SimpleNamespace(
        id=99102,
        role_id=2,
        role=SimpleNamespace(code="STORE_ADMIN"),
        store_id=None,
        status=1,
        token_version=0,
    )

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        permissions = {"reconcile:read", "order:read"}
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=permissions)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                reconcile = await client.get(
                    "/api/reconcile/payments/batches", params={"store_id": 101}
                )
                assert reconcile.status_code == 403, reconcile.text

                archive = await client.get(
                    "/api/reconcile/orders/archived", params={"store_id": 101}
                )
                assert archive.status_code == 403, archive.text
    finally:
        app.dependency_overrides.clear()



@pytest.mark.asyncio
async def test_failed_payment_reconcile_batch_is_committed_before_http_error(db_session):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = SimpleNamespace(
        id=99103,
        role_id=1,
        role=SimpleNamespace(code="SUPER_ADMIN"),
        store_id=None,
        status=1,
        token_version=0,
    )

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        permissions = {"reconcile:manage"}
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value=permissions)):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/reconcile/payments/import",
                    data={"channel": "WECHAT", "bill_date": "2026-11-26"},
                    files={"file": ("invalid.csv", b"not,a,valid,bill", "text/csv")},
                )
        assert response.status_code == 400, response.text
        persisted = (
            await db_session.execute(
                select(PaymentReconcileBatch).where(
                    PaymentReconcileBatch.bill_date == date(2026, 11, 26),
                    PaymentReconcileBatch.channel == "WECHAT",
                    PaymentReconcileBatch.status == "FAILED",
                )
            )
        ).scalar_one()
        assert persisted.error_message == "渠道账单缺少交易号或金额列"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_invalid_reimport_preserves_completed_reconcile_batch_and_diffs(db_session):
    from app.api.deps import get_current_user, get_db
    from app.main import app

    bill_date = date(2026, 11, 25)
    user = SimpleNamespace(
        id=99104,
        role_id=1,
        role=SimpleNamespace(code="SUPER_ADMIN"),
        store_id=None,
        status=1,
        token_version=0,
    )
    batch = PaymentReconcileBatch(
        channel="WECHAT",
        bill_date=bill_date,
        scope_store_id=0,
        status="DONE",
        local_count=8,
        channel_count=9,
        mismatch_count=1,
        source_filename="previous-valid.csv",
    )
    db_session.add(batch)
    await db_session.flush()
    old_diff = PaymentReconcileDiff(
        batch_id=batch.id,
        diff_type="LONG",
        channel_trade_no="PRESERVE-OLD-DIFF",
    )
    db_session.add(old_diff)
    await db_session.commit()
    batch_id = batch.id

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch(
            "app.api.deps.get_user_permissions",
            AsyncMock(return_value={"reconcile:manage"}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/reconcile/payments/import",
                    data={"channel": "WECHAT", "bill_date": bill_date.isoformat()},
                    files={"file": ("invalid-retry.csv", b"not,a,valid,bill", "text/csv")},
                )
        assert response.status_code == 400, response.text
        await db_session.refresh(batch)
        assert batch.status == "DONE"
        assert batch.source_filename == "previous-valid.csv"
        assert batch.mismatch_count == 1
        preserved = (
            await db_session.execute(
                select(PaymentReconcileDiff).where(
                    PaymentReconcileDiff.batch_id == batch_id
                )
            )
        ).scalars().all()
        assert [row.channel_trade_no for row in preserved] == ["PRESERVE-OLD-DIFF"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_first_reconcile_batch_observes_innodb_unique_key_wait(db_session):
    """Two RR sessions safely create the first batch; assert the real InnoDB wait graph."""
    import asyncio
    import uuid

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import PaymentReconcileBatch
    from app.services.payment_reconcile_service import PaymentReconcileService

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:12]
    bill_day = date(2026, 11, 26)
    winner_name = f"winner-{suffix}.csv"
    loser_name = f"loser-{suffix}.csv"
    task = None
    async with maker() as winner, maker() as loser:
        key = (
            PaymentReconcileBatch.channel == "WECHAT",
            PaymentReconcileBatch.bill_date == bill_day,
            PaymentReconcileBatch.scope_store_id == 0,
        )
        assert await winner.scalar(select(PaymentReconcileBatch.id).where(*key)) is None
        assert await loser.scalar(select(PaymentReconcileBatch.id).where(*key)) is None
        try:
            first = await PaymentReconcileService.run_batch(
                winner, channel="WECHAT", bill_date=bill_day,
                bill_csv="trade_no,amount\n", created_by=None,
                source_filename=winner_name, scope_store_id=0,
            )
            task = asyncio.create_task(
                PaymentReconcileService.run_batch(
                    loser, channel="WECHAT", bill_date=bill_day,
                    bill_csv="trade_no,amount\n", created_by=None,
                    source_filename=loser_name, scope_store_id=0,
                )
            )
            wait_sql = text("""
                SELECT COUNT(*)
                FROM performance_schema.data_lock_waits AS w
                JOIN performance_schema.data_locks AS req
                  ON req.ENGINE = w.ENGINE
                 AND req.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
                WHERE req.OBJECT_SCHEMA = DATABASE()
                  AND req.OBJECT_NAME = 'payment_reconcile_batches'
            """)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 10
            observed_wait = False
            async with engine.connect() as observer:
                while loop.time() < deadline:
                    waiting = int((await observer.execute(wait_sql)).scalar_one())
                    if waiting > 0:
                        observed_wait = True
                        break
                    if task.done():
                        await task
                        break
                    await asyncio.sleep(0.05)
            assert observed_wait, "InnoDB data_lock_waits did not show the competing batch INSERT"
            assert not task.done(), "competing INSERT completed before the winner committed"
            await winner.commit()
            second = await asyncio.wait_for(task, timeout=15)
            assert second.id == first.id
            assert second.status == "DONE"
            await loser.commit()
        finally:
            if task is not None and not task.done():
                await winner.rollback()
                try:
                    await asyncio.wait_for(task, timeout=3)
                except Exception:
                    task.cancel()
            if winner.in_transaction():
                await winner.rollback()
            if loser.in_transaction():
                await loser.rollback()

    async with maker() as cleanup:
        await cleanup.execute(
            PaymentReconcileBatch.__table__.delete().where(
                PaymentReconcileBatch.channel == "WECHAT",
                PaymentReconcileBatch.bill_date == bill_day,
                PaymentReconcileBatch.scope_store_id == 0,
                PaymentReconcileBatch.source_filename.in_([winner_name, loser_name]),
            )
        )
        await cleanup.commit()
