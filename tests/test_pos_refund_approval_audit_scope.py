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
