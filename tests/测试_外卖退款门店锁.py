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
    db.get = AsyncMock(side_effect=[d_order, None])
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
    db.get = AsyncMock(side_effect=[d_order, order, order, d_order])
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    mock_provider = MagicMock()
    mock_provider.cancel_order = AsyncMock(return_value=True)

    with patch(
        "app.services.delivery_service.get_credentials_for_store",
        AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.services.delivery_service.get_delivery_provider",
        return_value=mock_provider,
    ), patch(
        "app.services.order_lifecycle.apply_order_status",
        new_callable=AsyncMock,
    ) as apply:
        out = await DeliveryService.cancel_by_merchant(db, 2, "售罄")

    assert out["ok"] is True
    db.commit.assert_awaited()
    mock_provider.cancel_order.assert_awaited_once()
    apply.assert_awaited()
