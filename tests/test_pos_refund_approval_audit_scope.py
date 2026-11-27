"""POS refund approval audit records retain their store scope while pending."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import pos as pos_api


@pytest.mark.asyncio
async def test_processing_refund_approval_audit_keeps_request_store_scope(monkeypatch):
    row = SimpleNamespace(
        id=31,
        store_id=7,
        order_id=41,
        amount="18.00",
        status="PROCESSING",
    )
    monkeypatch.setattr(
        pos_api.PosRefundService,
        "approve_and_execute",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        pos_api.PosRefundService,
        "payload",
        AsyncMock(return_value={"status": row.status}),
    )
    audit = AsyncMock()
    monkeypatch.setattr(pos_api.AuditService, "log_staff", audit)
    monkeypatch.setattr(pos_api, "success", lambda data, request=None: data)

    response = await pos_api.approve_pos_refund(
        request=MagicMock(),
        db=MagicMock(),
        user=SimpleNamespace(id=5),
        request_id=31,
        data=pos_api.PosRefundReview(remark="平台处理中"),
    )

    assert response["status"] == "PROCESSING"
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["store_id"] == 7
    assert kwargs["detail"]["store_id"] == 7
    assert kwargs["target_id"] == 31


@pytest.mark.asyncio
async def test_refund_requester_cannot_approve_own_request(monkeypatch):
    from types import SimpleNamespace

    from app.core.exceptions import AppException
    from app.services import pos_refund_service
    from app.services.pos_refund_service import PosRefundService

    user = SimpleNamespace(id=5)
    row = SimpleNamespace(
        id=32,
        store_id=7,
        requested_by=5,
        status="PENDING",
        refund_id=None,
    )
    monkeypatch.setattr(pos_refund_service, "_can_approve", AsyncMock(return_value=True))
    monkeypatch.setattr(PosRefundService, "_lock_request", AsyncMock(return_value=row))
    monkeypatch.setattr(pos_refund_service, "assert_store_access", lambda *_args: None)

    with pytest.raises(AppException) as exc:
        await PosRefundService.approve_and_execute(
            MagicMock(), user=user, request_id=row.id, remark="self approval attempt"
        )
    assert exc.value.code == 40301
    assert "不能审批自己的" in exc.value.message


@pytest.mark.asyncio
async def test_missing_linked_refund_requires_reconciliation(monkeypatch):
    from types import SimpleNamespace

    from app.services import pos_refund_service
    from app.services.pos_refund_service import PosRefundService

    user = SimpleNamespace(id=9)
    row = SimpleNamespace(
        id=33,
        store_id=7,
        order_id=41,
        amount="18.00",
        status="PROCESSING",
        requested_by=None,
        refund_id=9999,
        execution_epoch=1,
        execution_lease_until=None,
        fail_reason=None,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(pos_refund_service, "_can_approve", AsyncMock(return_value=True))
    monkeypatch.setattr(PosRefundService, "_lock_request", AsyncMock(return_value=row))
    monkeypatch.setattr(pos_refund_service, "assert_store_access", lambda *_args: None)

    result = await PosRefundService.approve_and_execute(
        db, user=user, request_id=row.id, remark="核对异常关联"
    )

    assert result.status == "RECONCILIATION_REQUIRED"
    assert "关联退款单不存在" in result.fail_reason
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_missing_linked_refund_requires_reconciliation(monkeypatch):
    from types import SimpleNamespace

    from app.services.pos_refund_service import PosRefundService

    row = SimpleNamespace(
        id=34,
        status="PROCESSING",
        refund_id=10001,
        execution_token="claim-token",
        execution_epoch=3,
        execution_lease_until=None,
        executed_amount="0.00",
        fail_reason=None,
    )
    db = MagicMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock(return_value=None)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(
        PosRefundService, "_lock_request", AsyncMock(return_value=row)
    )

    result = await PosRefundService._recover_execution_error(
        db,
        request_id=row.id,
        claim_token="claim-token",
        claim_epoch=3,
        amount_total=Decimal("18.00"),
        error=RuntimeError("channel response lost"),
    )

    assert result.status == "RECONCILIATION_REQUIRED"
    assert "禁止重试" in result.fail_reason
    db.rollback.assert_awaited_once()
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
