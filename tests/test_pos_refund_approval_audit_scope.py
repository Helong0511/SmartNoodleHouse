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
