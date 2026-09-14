"""收银退款申请：组合支付分摊与权限。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.core.response import money
from app.services.pos_refund_service import PosRefundService


def _user(role_code: str, uid: int = 1, store_id: int = 1):
    u = MagicMock()
    u.id = uid
    u.role.code = role_code
    u.store_id = store_id
    return u


def _payment(*, pid: int, provider: str, amount: str, operator_id: int = 1):
    p = MagicMock()
    p.id = pid
    p.payment_no = f"P{pid}"
    p.provider = provider
    p.amount = Decimal(amount)
    p.operator_id = operator_id
    p.status = "PAID"
    return p


def test_allocate_cash_first_then_alipay():
    """退 10：现金 9 + 支付宝 1（现金优先）。"""
    cash = _payment(pid=2, provider="CASH", amount="9.00")
    ali = _payment(pid=1, provider="ALIPAY", amount="12.00")
    pairs = [(ali, money("12.00")), (cash, money("9.00"))]
    alloc = PosRefundService._allocate(money("10.00"), pairs)
    assert [(p.provider, str(a)) for p, a in alloc] == [
        ("CASH", "9.00"),
        ("ALIPAY", "1.00"),
    ]


def test_allocate_full_mixed_order():
    cash = _payment(pid=2, provider="CASH", amount="9.00")
    ali = _payment(pid=1, provider="ALIPAY", amount="12.00")
    pairs = [(ali, money("12.00")), (cash, money("9.00"))]
    alloc = PosRefundService._allocate(money("21.00"), pairs)
    assert sum((a for _, a in alloc), money(0)) == money("21.00")


def test_allocate_rejects_over_refundable():
    cash = _payment(pid=2, provider="CASH", amount="9.00")
    pairs = [(cash, money("9.00"))]
    with pytest.raises(AppException) as exc:
        PosRefundService._allocate(money("10.00"), pairs)
    assert exc.value.code == 40052


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

    payment = _payment(pid=20, provider="CASH", amount="21.00", operator_id=99)

    db.get = AsyncMock(return_value=order)
    with patch("app.services.pos_refund_service.assert_order_store_access"), patch.object(
        PosRefundService, "_list_paid_payments", new_callable=AsyncMock, return_value=[payment]
    ):
        with pytest.raises(AppException) as exc:
            await PosRefundService.create_request(
                db, user=_user("CASHIER", uid=1), order_id=10, amount=None, reason="顾客不要了"
            )
    assert exc.value.code == 40301


@pytest.mark.asyncio
async def test_create_request_uses_order_level_refundable():
    """组合单可退按支付宝+现金合计，不再只认最后一笔现金。"""
    db = AsyncMock()
    order = MagicMock()
    order.id = 10
    order.channel = "POS"
    order.store_id = 1
    order.payment_status = "PAID"
    order.status = "PAID"
    order.table_id = None

    ali = _payment(pid=1, provider="ALIPAY", amount="12.00", operator_id=1)
    cash = _payment(pid=2, provider="CASH", amount="9.00", operator_id=1)

    db.get = AsyncMock(return_value=order)
    db.scalar = AsyncMock(return_value=0)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch("app.services.pos_refund_service.assert_order_store_access"), patch.object(
        PosRefundService, "_list_paid_payments", new_callable=AsyncMock, return_value=[ali, cash]
    ), patch.object(
        PosRefundService,
        "_refundable_pairs",
        new_callable=AsyncMock,
        return_value=(money("21.00"), [(ali, money("12.00")), (cash, money("9.00"))]),
    ):
        row = await PosRefundService.create_request(
            db, user=_user("CASHIER", uid=1), order_id=10, amount=Decimal("10.00"), reason="客户退款"
        )
    assert row.amount == money("10.00")
    # 分摊首笔为现金
    assert row.payment_id == cash.id
    db.add.assert_called_once()


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


def _lock_result(row):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    return result


@pytest.mark.asyncio
async def test_cancel_request_uses_row_lock():
    """撤销必须 FOR UPDATE，与审批互斥。"""
    row = MagicMock()
    row.id = 7
    row.requested_by = 1
    row.store_id = 1
    row.status = "PENDING"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_lock_result(row))
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    out = await PosRefundService.cancel_request(db, user=_user("CASHIER", uid=1), request_id=7)
    assert out is row
    assert row.status == "CANCELLED"
    stmt = db.execute.call_args.args[0]
    assert stmt._for_update_arg is not None


@pytest.mark.asyncio
async def test_cancel_request_blocked_after_concurrent_approve():
    """行锁等到审批事务提交后，当前读为 SUCCESS → 撤销必须失败（不得覆写退款结果）。"""
    row = MagicMock()
    row.id = 7
    row.requested_by = 1
    row.store_id = 1
    row.status = "SUCCESS"  # 锁等待后看到审批已提交
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_lock_result(row))

    with pytest.raises(AppException) as exc:
        await PosRefundService.cancel_request(db, user=_user("CASHIER", uid=1), request_id=7)
    assert exc.value.code == 40014


@pytest.mark.asyncio
async def test_reject_request_uses_row_lock_and_blocks_when_approved():
    """拒绝同样 FOR UPDATE；锁后状态非 PENDING 必须拒绝。"""
    row = MagicMock()
    row.id = 8
    row.store_id = 1
    row.status = "PROCESSING"  # 审批已在调用外部退款渠道
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_lock_result(row))

    with patch("app.services.pos_refund_service.assert_store_access"):
        with pytest.raises(AppException) as exc:
            await PosRefundService.reject_request(
                db, user=_user("STORE_ADMIN"), request_id=8, remark="no"
            )
    assert exc.value.code == 40014
    stmt = db.execute.call_args.args[0]
    assert stmt._for_update_arg is not None
