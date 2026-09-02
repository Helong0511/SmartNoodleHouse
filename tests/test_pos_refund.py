"""收银退款申请服务测试。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.pos_refund_service import PosRefundService


def _user(role_code: str, uid: int = 1, store_id: int = 1):
    u = MagicMock()
    u.id = uid
    u.role.code = role_code
    u.store_id = store_id
    return u


@pytest.mark.asyncio
async def test_cashier_cannot_apply_others_payment():
    db = AsyncMock()
    order = MagicMock()
    order.id = 10
    order.channel = "POS"
    order.store_id = 1
    order.payment_status = "PAID"
    order.status = "PAID"
    order.table_id = None

    payment = MagicMock()
    payment.id = 20
    payment.operator_id = 99  # 别人收的

    db.get = AsyncMock(return_value=order)
    with patch("app.services.pos_refund_service.assert_order_store_access"), patch.object(
        PosRefundService, "_paid_payment_for_order", new_callable=AsyncMock, return_value=payment
    ):
        with pytest.raises(AppException) as exc:
            await PosRefundService.create_request(
                db, user=_user("CASHIER", uid=1), order_id=10, amount=None, reason="顾客不要了"
            )
    assert exc.value.code == 40301


@pytest.mark.asyncio
async def test_reject_requires_manager():
    db = AsyncMock()
    with pytest.raises(AppException) as exc:
        await PosRefundService.reject_request(
            db, user=_user("CASHIER"), request_id=1, remark="no"
        )
    assert exc.value.code == 40301


@pytest.mark.asyncio
async def test_approve_requires_manager():
    db = AsyncMock()
    with pytest.raises(AppException) as exc:
        await PosRefundService.approve_and_execute(
            db, user=_user("CASHIER"), request_id=1
        )
    assert exc.value.code == 40301
