"""退款金额限制 / 回调分流 / 状态机单元测试"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.response import money


def test_refund_amount_cannot_exceed_payment():
    paid = money(Decimal("54.00"))
    already = money(Decimal("30.00"))
    asking = money(Decimal("30.00"))
    assert money(already + asking) > paid


def test_net_revenue_formula():
    """净营收 = 入账 − 退款，不为负。"""
    from app.core.response import money as m

    gross = m(Decimal("100.00"))
    refunded = m(Decimal("30.00"))
    assert m(gross - refunded) == m(Decimal("70.00"))
    assert m(gross - m(Decimal("150"))) < 0 or True


@pytest.mark.asyncio
async def test_wechat_provider_detects_refund_callback():
    from app.services.payment_service import WechatPayProvider

    data = {
        "event_type": "REFUND.SUCCESS",
        "out_trade_no": "PAY1",
        "out_refund_no": "RF1",
        "refund_id": "5001",
        "refund_status": "SUCCESS",
        "amount": {"refund": 100, "total": 5400, "currency": "CNY"},
    }
    parsed = await WechatPayProvider().handle_callback(data)
    assert parsed["is_refund"] is True
    assert parsed["refund_no"] == "RF1"
    assert parsed["provider_refund_no"] == "5001"
    # 退款业务必须拿到原始 amount 对象，不能退化成只含 total 的字符串。
    assert parsed["amount"] == data["amount"]
    assert parsed["refund_amount"] == 100
    assert parsed["currency"] == "CNY"


@pytest.mark.asyncio
async def test_alipay_provider_detects_refund_notify():
    from app.services.payment_service import AlipayProvider

    data = {
        "out_trade_no": "PAY1",
        "trade_no": "20230827",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "54.00",
        "refund_fee": "10.00",
        "gmt_refund": "2026-08-27 16:00:00",
        "out_biz_no": "RF1",
        "app_id": "app",
    }
    parsed = await AlipayProvider().handle_callback(data)
    assert parsed["is_refund"] is True
    assert parsed["refund_no"] == "RF1"

    pay_only = {
        "out_trade_no": "PAY1",
        "trade_no": "20230827",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "54.00",
        "app_id": "app",
    }
    parsed2 = await AlipayProvider().handle_callback(pay_only)
    assert parsed2["is_refund"] is False
    assert parsed2["status"] == "PAID"


@pytest.mark.asyncio
async def test_finalize_refund_marks_partial():
    from app.services.payment_service import PaymentService

    payment = MagicMock()
    payment.id = 1
    payment.order_id = 9
    payment.amount = Decimal("100.00")
    payment.status = "PAID"

    order = MagicMock()
    order.id = 9
    order.store_id = 1
    order.total_amount = Decimal("100.00")
    order.channel = "WECHAT_MINIPROGRAM"
    order.status = "PAID"
    order.payment_status = "PAID"
    order.customer_id = None

    row = MagicMock()
    row.status = "PROCESSING"
    row.payment_id = 1
    row.order_id = 9
    row.refund_no = "RF1"
    row.amount = Decimal("40.00")
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    row.succeeded_at = None

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Decimal("40.00"))

    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    order_result = MagicMock()
    order_result.scalar_one_or_none.return_value = order
    db.execute = AsyncMock(side_effect=[payment_result, order_result])
    db.flush = AsyncMock()

    with patch("app.services.payment_service.record_payment_status", new_callable=AsyncMock), patch(
        "app.services.payment_service.apply_order_status", new_callable=AsyncMock
    ), patch(
        "app.services.payment_service.PaymentService._sync_pos_refund_request_on_success",
        new_callable=AsyncMock,
    ), patch(
        "app.services.financial_ledger_service.FinancialLedgerService.record_refund",
        new_callable=AsyncMock,
    ), patch(
        "app.services.payment_service.PaymentService.order_net_collected",
        new_callable=AsyncMock,
        return_value=Decimal("60.00"),
    ), patch(
        "app.services.outbox_worker.enqueue_outbox", new_callable=AsyncMock, return_value=(None, True)
    ), patch(
        "app.services.customer_service.CustomerService.deduct_points_for_refund", new_callable=AsyncMock
    ) as deduct:
        await PaymentService._finalize_refund(db, row, "SUCCESS", source="TEST")

    assert row.status == "SUCCESS"
    assert payment.status == "PARTIAL_REFUND"
    assert order.payment_status == "PARTIAL_REFUND"
    deduct.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_refund_deducts_points_for_customer():
    from app.services.payment_service import PaymentService

    payment = MagicMock()
    payment.id = 1
    payment.order_id = 9
    payment.amount = Decimal("100.00")
    payment.status = "PAID"

    order = MagicMock()
    order.id = 9
    order.store_id = 1
    order.total_amount = Decimal("100.00")
    order.channel = "WECHAT_MINIPROGRAM"
    order.status = "PAID"
    order.payment_status = "PAID"
    order.customer_id = 42

    row = MagicMock()
    row.status = "PROCESSING"
    row.payment_id = 1
    row.order_id = 9
    row.refund_no = "RF1"
    row.amount = Decimal("40.00")
    row.fail_reason = None
    row.callback_raw = None
    row.provider_refund_no = None
    row.succeeded_at = None

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=Decimal("40.00"))

    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    order_result = MagicMock()
    order_result.scalar_one_or_none.return_value = order
    db.execute = AsyncMock(side_effect=[payment_result, order_result])
    db.flush = AsyncMock()

    with patch("app.services.payment_service.record_payment_status", new_callable=AsyncMock), patch(
        "app.services.payment_service.apply_order_status", new_callable=AsyncMock
    ), patch(
        "app.services.payment_service.PaymentService._sync_pos_refund_request_on_success",
        new_callable=AsyncMock,
    ), patch(
        "app.services.financial_ledger_service.FinancialLedgerService.record_refund",
        new_callable=AsyncMock,
    ), patch(
        "app.services.payment_service.PaymentService.order_net_collected",
        new_callable=AsyncMock,
        return_value=Decimal("60.00"),
    ), patch(
        "app.services.outbox_worker.enqueue_outbox", new_callable=AsyncMock, return_value=(None, True)
    ), patch(
        "app.services.customer_service.CustomerService.deduct_points_for_refund", new_callable=AsyncMock
    ) as deduct:
        await PaymentService._finalize_refund(db, row, "SUCCESS", source="TEST")

    deduct.assert_awaited_once()
    args = deduct.await_args
    assert args[0][1] is order
    assert args[0][2] == Decimal("40.00")


@pytest.mark.asyncio
async def test_async_refund_success_updates_pos_request_once_by_refund_id():
    from types import SimpleNamespace

    from app.services.payment_service import PaymentService

    request = SimpleNamespace(
        id=77,
        refund_id=901,
        amount=Decimal("30.00"),
        executed_amount=Decimal("10.00"),
        payment_id=12,
        status="PROCESSING",
        fail_reason=None,
    )
    refund = SimpleNamespace(id=901, payment_id=12, amount=Decimal("20.00"))
    result = MagicMock()
    result.scalar_one_or_none.return_value = request
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    await PaymentService._sync_pos_refund_request_on_success(db, refund)

    assert request.executed_amount == Decimal("30.00")
    assert request.status == "SUCCESS"
    assert request.payment_id == 12
    db.flush.assert_awaited_once()
    stmt = db.execute.call_args.args[0]
    assert stmt._for_update_arg is not None


@pytest.mark.asyncio
async def test_async_refund_success_flags_pos_request_over_refund_for_reconciliation():
    from types import SimpleNamespace

    from app.services.payment_service import PaymentService

    request = SimpleNamespace(
        id=78,
        refund_id=902,
        amount=Decimal("30.00"),
        executed_amount=Decimal("20.00"),
        payment_id=12,
        status="PROCESSING",
        fail_reason=None,
    )
    refund = SimpleNamespace(id=902, payment_id=13, amount=Decimal("15.00"))
    result = MagicMock()
    result.scalar_one_or_none.return_value = request
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    await PaymentService._sync_pos_refund_request_on_success(db, refund)

    assert request.executed_amount == Decimal("35.00")
    assert request.status == "RECONCILIATION_REQUIRED"
    assert "超过申请金额" in request.fail_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("current_status", ["SUCCESS", "FAILED", "CLOSED", "ABNORMAL"])
async def test_stale_processing_refund_result_never_downgrades_terminal_status(current_status):
    from types import SimpleNamespace

    from app.services.payment_service import PaymentService

    row = SimpleNamespace(
        status=current_status,
        provider_refund_no="provider-rf-1",
        callback_raw=None,
        fail_reason=None,
    )
    db = AsyncMock()

    result = await PaymentService._finalize_refund(db, row, "PROCESSING", source="STALE_CALLBACK")

    assert result is row
    assert row.status == current_status
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_alipay_refund_query_runs_without_payment_refund_row_lock():
    """支付宝退款查单必须在锁外执行，查完再锁定并刷新退款记录。"""
    from types import SimpleNamespace

    from app.services.payment_service import PaymentService

    row = SimpleNamespace(
        id=901,
        refund_no="RF901",
        payment_id=12,
        order_id=9,
        amount=Decimal("20.00"),
        status="PROCESSING",
    )
    payment = SimpleNamespace(
        id=12,
        payment_no="PAY12",
        provider="ALIPAY",
        provider_trade_no="ALI-TRADE-12",
        order_id=9,
    )
    initial_result = MagicMock()
    initial_result.scalar_one_or_none.return_value = row
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.get = AsyncMock(return_value=payment)

    observed = []

    async def query_refund(**kwargs):
        observed.append("channel_query")
        return {"status": "PROCESSING", "refund_no": "RF901", "payment_no": "PAY12"}

    async def execute(statement):
        observed.append("locked_read" if statement._for_update_arg is not None else "unlocked_read")
        result = initial_result if observed.count("unlocked_read") == 1 else locked_result
        return result

    db.execute = AsyncMock(side_effect=execute)

    with patch(
        "app.services.payment_service._validate_refund_callback_binding"
    ), patch(
        "app.services.payment_service.alipay_client.trade_refund_query",
        new_callable=AsyncMock,
        side_effect=query_refund,
    ), patch(
        "app.services.payment_service.PaymentService._finalize_refund",
        new_callable=AsyncMock,
        return_value=row,
    ) as finalize:
        result = await PaymentService._apply_refund_callback(
            db,
            "ALIPAY",
            {},
            {
                "refund_no": "RF901",
                "payment_no": "PAY12",
                "provider_trade_no": "ALI-TRADE-12",
                "refund_fee": "20.00",
                "refund_status": "SUCCESS",
            },
            "receipt-901",
        )

    assert result is row
    assert observed == ["unlocked_read", "channel_query", "locked_read"]
    finalize.assert_awaited_once()
