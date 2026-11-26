"""Regression tests for reconciliation and archive store-level authorization."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
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

    async def fail_after_recording_batch(
        db, *, channel, bill_date, bill_csv, created_by=None,
        source_filename=None, scope_store_id=0,
    ):
        batch = PaymentReconcileBatch(
            channel=channel.upper(),
            bill_date=bill_date,
            scope_store_id=scope_store_id,
            status="FAILED",
            created_by=created_by,
            source_filename=source_filename,
            error_message="渠道账单缺少交易号列",
        )
        db.add(batch)
        await db.flush()
        raise ValueError(batch.error_message)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        permissions = {"reconcile:manage"}
        with (
            patch("app.api.deps.get_user_permissions", AsyncMock(return_value=permissions)),
            patch(
                "app.api.reconcile.PaymentReconcileService.run_batch",
                side_effect=fail_after_recording_batch,
            ),
        ):
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
        assert persisted.error_message == "渠道账单缺少交易号列"
    finally:
        app.dependency_overrides.clear()
