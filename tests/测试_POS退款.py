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
    db.get.assert_awaited_once_with(__import__("app.models", fromlist=["Order"]).Order, 10, with_for_update=True)


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
    db.get.assert_awaited_once_with(__import__("app.models", fromlist=["Order"]).Order, 10, with_for_update=True)
    pending_stmt = db.scalar.await_args.args[0]
    pending_params = pending_stmt.compile().params
    pending_statuses = next(v for k, v in pending_params.items() if "status" in k)
    assert set(pending_statuses) == {"PENDING", "PROCESSING", "RECONCILIATION_REQUIRED"}


@pytest.mark.asyncio
async def test_reject_requires_manager():
    db = AsyncMock()
    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with pytest.raises(AppException) as exc:
            await PosRefundService.reject_request(
                db, user=_user("CASHIER"), request_id=1, remark="no"
            )
    assert exc.value.code == 40301


@pytest.mark.asyncio
async def test_approve_requires_manager():
    db = AsyncMock()
    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock,
        return_value=False,
    ):
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


@pytest.mark.asyncio
async def test_approve_claims_processing_before_channel_refund():
    """审批：先 PROCESSING+commit，再调渠道退款，避免 PENDING 被并发撤销。"""
    from decimal import Decimal
    from types import SimpleNamespace

    from app.core.response import money

    payment = SimpleNamespace(
        id=3,
        provider="WECHAT",
        status="PAID",
        amount=money("10.00"),
        order_id=9,
    )
    row = SimpleNamespace(
        id=12,
        store_id=1,
        order_id=9,
        amount=money("10.00"),
        executed_amount=money("0"),
        reason="测",
        status="PENDING",
        reviewed_by=None,
        reviewed_at=None,
        review_remark=None,
        payment_id=3,
        refund_id=None,
        fail_reason=None,
    )
    db = AsyncMock()
    refund_row = SimpleNamespace(id=99, status="SUCCESS", fail_reason=None)
    request_result = _lock_result(row)
    refund_result = _lock_result(refund_row)
    db.execute = AsyncMock(side_effect=[request_result, refund_result, request_result, request_result])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.get = AsyncMock(return_value=payment)

    refund_mock = AsyncMock(return_value=refund_row)

    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.pos_refund_service.assert_store_access"
    ), patch.object(
        PosRefundService,
        "_refundable_pairs",
        new_callable=AsyncMock,
        return_value=(money("10.00"), [(payment, money("10.00"))]),
    ), patch(
        "app.services.pos_refund_service.PaymentService.refund",
        refund_mock,
    ):
        out = await PosRefundService.approve_and_execute(
            db, user=_user("STORE_ADMIN"), request_id=12
        )

    assert db.commit.await_count >= 1
    # 首次 commit 时已认领 PROCESSING（refund 之前）
    assert refund_mock.await_count == 1
    assert out.status == "SUCCESS"


@pytest.mark.asyncio
async def test_processing_pos_request_with_inflight_refund_never_reissues_provider_refund():
    """已绑定的退款仍在渠道处理中，重复点击审批只返回现状，不新建第二笔退款。"""
    from types import SimpleNamespace

    request = SimpleNamespace(
        id=120,
        store_id=1,
        order_id=90,
        amount=money("20.00"),
        executed_amount=money("0.00"),
        reason="顾客退款",
        status="PROCESSING",
        reviewed_by=None,
        reviewed_at=None,
        review_remark=None,
        payment_id=3,
        refund_id=901,
        fail_reason=None,
    )
    linked_refund = SimpleNamespace(id=901, status="UNKNOWN")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_lock_result(request))
    db.get = AsyncMock(return_value=linked_refund)
    db.refresh = AsyncMock()

    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock,
        return_value=True,
    ), patch("app.services.pos_refund_service.assert_store_access"), patch(
        "app.services.pos_refund_service.PaymentService.refund",
        new_callable=AsyncMock,
    ) as refund_call:
        out = await PosRefundService.approve_and_execute(
            db, user=_user("STORE_ADMIN"), request_id=120
        )

    assert out is request
    assert out.status == "PROCESSING"
    refund_call.assert_not_awaited()
    db.refresh.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_processing_pos_request_without_refund_binding_never_starts_second_execution():
    """认领事务已提交但首笔退款单尚未绑定时，重复审批必须 fail-closed。"""
    from types import SimpleNamespace

    request = SimpleNamespace(
        id=121,
        store_id=1,
        order_id=91,
        amount=money("20.00"),
        executed_amount=money("0.00"),
        reason="顾客退款",
        status="PROCESSING",
        reviewed_by=8,
        reviewed_at=None,
        review_remark=None,
        payment_id=3,
        refund_id=None,
        fail_reason=None,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_lock_result(request))
    db.refresh = AsyncMock()

    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock,
        return_value=True,
    ), patch("app.services.pos_refund_service.assert_store_access"), patch.object(
        PosRefundService, "_refundable_pairs", new_callable=AsyncMock
    ) as allocate, patch(
        "app.services.pos_refund_service.PaymentService.refund",
        new_callable=AsyncMock,
    ) as refund_call:
        out = await PosRefundService.approve_and_execute(
            db, user=_user("STORE_ADMIN"), request_id=121
        )

    assert out is request
    assert out.status == "PROCESSING"
    db.refresh.assert_awaited_once_with(request)
    allocate.assert_not_awaited()
    refund_call.assert_not_awaited()

@pytest.mark.asyncio
async def test_recovery_preserves_processing_when_refund_row_is_already_persisted():
    """异常发生在退款单提交之后时，POS 申请不能被错误标为 FAILED。"""
    from types import SimpleNamespace

    request = SimpleNamespace(
        id=130,
        status="PROCESSING",
        refund_id=990,
        execution_token="owner-current",
        execution_epoch=7,
        execution_lease_until=None,
        executed_amount=money("0.00"),
        amount=money("20.00"),
        fail_reason=None,
    )
    refund = SimpleNamespace(status="PENDING")
    db = AsyncMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock(return_value=refund)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    with patch.object(PosRefundService, "_lock_request", new_callable=AsyncMock, return_value=request):
        out = await PosRefundService._recover_execution_error(
            db,
            request_id=130,
            claim_token="owner-current",
            claim_epoch=7,
            amount_total=money("20.00"),
            error=RuntimeError("local persistence interrupted after refund row commit"),
        )

    assert out is request
    assert request.status == "PROCESSING"
    assert "待确认" in request.fail_reason
    db.rollback.assert_awaited_once()
    db.get.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_pos_refund_owner_cannot_overwrite_new_execution_state():
    """租约接管后，旧 worker 的异常收尾不得覆盖新 epoch 的状态。"""
    from types import SimpleNamespace

    request = SimpleNamespace(
        id=131,
        status="PROCESSING",
        refund_id=None,
        execution_token="new-owner",
        execution_epoch=9,
        execution_lease_until=None,
        executed_amount=money("0.00"),
        amount=money("20.00"),
        fail_reason=None,
    )
    db = AsyncMock()
    db.rollback = AsyncMock()
    db.commit = AsyncMock()

    with patch.object(PosRefundService, "_lock_request", new_callable=AsyncMock, return_value=request):
        out = await PosRefundService._recover_execution_error(
            db,
            request_id=131,
            claim_token="old-owner",
            claim_epoch=8,
            amount_total=money("20.00"),
            error=RuntimeError("stale worker resumed"),
        )

    assert out is request
    assert request.status == "PROCESSING"
    assert request.execution_token == "new-owner"
    assert request.execution_epoch == 9
    db.get.assert_not_awaited()
    db.commit.assert_not_awaited()



@pytest.mark.asyncio
async def test_mixed_payment_refunds_reuse_one_fencing_claim_and_accumulate_once():
    """组合支付分两笔渠道退款时，同一审批必须复用 owner epoch，金额只累计一次。"""
    from types import SimpleNamespace

    cash = SimpleNamespace(
        id=301, provider="CASH", status="PAID", amount=money("4.00"), order_id=901,
    )
    wechat = SimpleNamespace(
        id=302, provider="WECHAT", status="PAID", amount=money("6.00"), order_id=901,
    )
    request = SimpleNamespace(
        id=303, store_id=1, order_id=901, amount=money("10.00"),
        executed_amount=money("0.00"), reason="组合支付退款", status="PENDING",
        reviewed_by=None, reviewed_at=None, review_remark=None,
        payment_id=301, refund_id=None, fail_reason=None,
        execution_epoch=4, execution_token=None, execution_lease_until=None,
    )
    refunds = [
        SimpleNamespace(id=401, status="SUCCESS", fail_reason=None),
        SimpleNamespace(id=402, status="SUCCESS", fail_reason=None),
    ]
    db = AsyncMock()
    request_result = _lock_result(request)
    refund_results = [_lock_result(refunds[0]), _lock_result(refunds[1])]
    # 初次锁申请；每笔退款后分别锁退款事实和 POS 申请；最后重新锁申请收敛状态。
    db.execute = AsyncMock(side_effect=[
        request_result,
        refund_results[0], request_result,
        refund_results[1], request_result,
        request_result,
    ])
    db.get = AsyncMock(side_effect=[cash, wechat])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    observed = []

    async def refund_mock(
        _db, payment, amount, reason, *, operator_id,
        pos_refund_request_id, pos_refund_execution_token,
        pos_refund_execution_epoch,
    ):
        observed.append((
            payment.id, amount, pos_refund_request_id,
            pos_refund_execution_token, pos_refund_execution_epoch,
        ))
        refund = refunds[len(observed) - 1]
        # 模拟 PaymentService 在退款成功事务内绑定退款单并累计执行金额。
        request.refund_id = refund.id
        request.payment_id = payment.id
        request.executed_amount = money(request.executed_amount + amount)
        return refund

    with patch(
        "app.services.pos_refund_service._can_approve",
        new_callable=AsyncMock, return_value=True,
    ), patch(
        "app.services.pos_refund_service.assert_store_access"
    ), patch.object(
        PosRefundService, "_refundable_pairs",
        new_callable=AsyncMock,
        return_value=(money("10.00"), [
            (wechat, money("6.00")), (cash, money("4.00")),
        ]),
    ), patch(
        "app.services.pos_refund_service.PaymentService.refund",
        side_effect=refund_mock,
    ):
        result = await PosRefundService.approve_and_execute(
            db, user=_user("STORE_ADMIN"), request_id=303,
        )

    assert [item[0] for item in observed] == [301, 302]
    assert [item[1] for item in observed] == [money("4.00"), money("6.00")]
    assert all(item[2] == 303 for item in observed)
    assert observed[0][3] and observed[0][3] == observed[1][3]
    assert observed[0][4] == observed[1][4] == 5
    assert request.executed_amount == money("10.00")
    assert result.status == "SUCCESS"
    assert db.commit.await_count >= 3


def test_pos_refund_execution_claim_matches_current_owner_only():
    """旧 worker 的迟到响应不能覆盖已接管的执行者关联。"""
    from types import SimpleNamespace
    from app.services.pos_refund_service import PosRefundService

    current = SimpleNamespace(execution_token="new-owner", execution_epoch=12)
    assert PosRefundService._execution_claim_matches(
        current, claim_token="new-owner", claim_epoch=12
    )
    assert not PosRefundService._execution_claim_matches(
        current, claim_token="old-owner", claim_epoch=11
    )
    assert not PosRefundService._execution_claim_matches(
        current, claim_token="new-owner", claim_epoch=11
    )
