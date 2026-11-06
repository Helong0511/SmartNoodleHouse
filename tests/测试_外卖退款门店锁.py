"""外卖退款/取消：禁止 store_id=1 兜底；退款须行锁。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.delivery_service import DeliveryService


@pytest.mark.asyncio
async def test_process_refund_requires_order_store():
    refund = SimpleNamespace(id=1, order_id=9, status="PENDING", delivery_order_id=2, platform="ELEME")
    db = MagicMock()
    locked = MagicMock(scalar_one_or_none=MagicMock(return_value=refund))
    db.execute = AsyncMock(return_value=locked)
    db.get = AsyncMock(side_effect=[SimpleNamespace(id=2), None])  # d_order, order missing
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    with pytest.raises(AppException) as ei:
        await DeliveryService.process_refund(db, 1, approve=True)
    assert ei.value.code == 40454
    db.execute.assert_awaited()  # with_for_update path
    db.commit.assert_not_awaited()  # 缺订单时不得认领


@pytest.mark.asyncio
async def test_process_refund_fail_closed_without_credentials():
    refund = SimpleNamespace(
        id=1,
        order_id=9,
        status="PENDING",
        delivery_order_id=2,
        platform="ELEME",
        platform_refund_id="pr1",
        refund_type="FULL",
        amount=10,
    )
    order = SimpleNamespace(id=9, store_id=7, status="COOKING")
    d_order = SimpleNamespace(id=2, order_id=9, platform_order_id="po1")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=refund)))
    db.get = AsyncMock(side_effect=[d_order, order])
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    with patch(
        "app.services.delivery_service.get_credentials_for_store",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(AppException) as ei:
            await DeliveryService.process_refund(db, 1, approve=True)
    assert ei.value.code == 40061
    assert "7" in ei.value.message
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_by_merchant_requires_order():
    d_order = SimpleNamespace(id=2, order_id=9, platform="MEITUAN", platform_order_id="po")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=9)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    ])
    db.commit = AsyncMock()
    with pytest.raises(AppException) as ei:
        await DeliveryService.cancel_by_merchant(db, 2, "库存不足")
    assert ei.value.code == 40454
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_by_merchant_http_after_commit():
    """商家取消：先 commit 再调平台，避免长事务。"""
    d_order = SimpleNamespace(
        id=2, order_id=9, platform="MEITUAN", platform_order_id="po", delivery_status="ACCEPTED"
    )
    order = SimpleNamespace(id=9, store_id=7, status="COOKING")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=9)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=order)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=d_order)),
    ])
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    outbox_event = SimpleNamespace(id=77, status="PENDING")

    with patch(
        "app.services.delivery_service.get_credentials_for_store",
        AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.services.delivery_service.enqueue_outbox",
        new=AsyncMock(return_value=(outbox_event, True)),
    ) as enqueue:
        out = await DeliveryService.cancel_by_merchant(db, 2, "售罄")

    assert out == {
        "ok": False, "status": "PENDING", "order_id": 9,
        "outbox_id": 77, "queued": True,
    }
    db.commit.assert_awaited_once()
    enqueue.assert_awaited_once()



@pytest.mark.asyncio
async def test_process_refund_recovers_ledger_without_calling_provider():
    """平台已退但本地账本失败时，重试只补账，不再次调用外卖平台。"""
    refund = SimpleNamespace(
        id=31, order_id=91, delivery_order_id=41, platform="MEITUAN",
        platform_refund_id="platform-rf-1", amount=12, refund_type="PARTIAL",
        status="APPROVED_LEDGER_PENDING", handled_at=None,
    )
    d_order = SimpleNamespace(id=41, order_id=91, refund_status="APPROVED_LEDGER_PENDING")
    order = SimpleNamespace(id=91, store_id=7, channel="MEITUAN")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=refund)))
    db.get = AsyncMock(side_effect=[d_order, order])
    db.commit = AsyncMock()

    with patch(
        "app.services.financial_ledger_service.FinancialLedgerService.record_refund",
        new=AsyncMock(return_value=SimpleNamespace(id=101)),
    ) as record, patch(
        "app.services.delivery_service.get_delivery_provider"
    ) as provider_factory:
        result = await DeliveryService.process_refund(db, 31, approve=True)

    assert result["status"] == "APPROVED"
    assert result["ledger_recovered"] is True
    assert refund.status == "APPROVED"
    assert d_order.refund_status == "APPROVED"
    record.assert_awaited_once()
    provider_factory.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_refund_ledger_recovery_failure_keeps_pending_repair_state():
    refund = SimpleNamespace(
        id=32, order_id=92, delivery_order_id=42, platform="ELEME",
        platform_refund_id="platform-rf-2", amount=8, refund_type="PARTIAL",
        status="APPROVED_LEDGER_PENDING", handled_at=None,
    )
    d_order = SimpleNamespace(id=42, order_id=92, refund_status="APPROVED_LEDGER_PENDING")
    order = SimpleNamespace(id=92, store_id=8, channel="ELEME")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=refund)))
    db.get = AsyncMock(side_effect=[d_order, order])
    db.rollback = AsyncMock()

    with patch(
        "app.services.financial_ledger_service.FinancialLedgerService.record_refund",
        new=AsyncMock(side_effect=RuntimeError("database unavailable")),
    ), pytest.raises(AppException) as ei:
        await DeliveryService.process_refund(db, 32, approve=True)

    assert ei.value.code == 50354
    assert refund.status == "APPROVED_LEDGER_PENDING"
    assert d_order.refund_status == "APPROVED_LEDGER_PENDING"
    db.rollback.assert_awaited_once()
