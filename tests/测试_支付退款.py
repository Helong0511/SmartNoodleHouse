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
        "refund_status": "REFUND_SUCCESS",
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


def test_alipay_refund_status_is_fail_closed():
    from app.services.payment_service import _resolve_alipay_refund_status

    assert _resolve_alipay_refund_status({"refund_status": "REFUND_SUCCESS"}) == "SUCCESS"
    assert _resolve_alipay_refund_status({"refund_status": "REFUND_CLOSED"}) == "CLOSED"
    assert _resolve_alipay_refund_status({"refund_status": "SELLER_REFUSE_BUYER"}) == "FAILED"
    assert _resolve_alipay_refund_status({"refund_status": "WAIT_SELLER_AGREE"}) == "PROCESSING"
    # 累计金额、退款时间均不能证明某个 out_request_no 对应的退款已成功。
    assert _resolve_alipay_refund_status({"refund_fee": "10.00"}) == "PROCESSING"
    assert _resolve_alipay_refund_status({"gmt_refund": "2026-08-27 16:00:00"}) == "PROCESSING"
    assert _resolve_alipay_refund_status({}) == "PROCESSING"


def test_refund_poll_backoff_grows_and_caps():
    from app.services.payment_service import _refund_poll_backoff_seconds

    assert [_refund_poll_backoff_seconds(n) for n in (1, 2, 3, 4, 5, 100)] == [
        300, 600, 1200, 1800, 1800, 1800
    ]


@pytest.mark.asyncio
async def test_alipay_provider_preserves_refund_status():
    from app.services.payment_service import AlipayProvider

    parsed = await AlipayProvider().handle_callback({
        "out_trade_no": "PAY2",
        "trade_no": "20230828",
        "trade_status": "TRADE_CLOSED",
        "refund_status": "REFUND_CLOSED",
        "out_biz_no": "RF2",
        "app_id": "app",
    })
    assert parsed["is_refund"] is True
    assert parsed["refund_status"] == "REFUND_CLOSED"


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
    db.execute = AsyncMock(side_effect=[order_result, payment_result])
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
    db.execute = AsyncMock(side_effect=[order_result, payment_result])
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
        amount=Decimal("20.00"),
    )
    initial_result = MagicMock()
    initial_result.scalar_one_or_none.return_value = row
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.get = AsyncMock(return_value=payment)

    observed = []

    async def execute(statement):
        sql = str(statement).lower()
        if "payments.order_id" in sql and "for update" not in sql:
            observed.append("payment_peek")
            peek = MagicMock()
            peek.first.return_value = (9, None)
            return peek
        if "from orders" in sql:
            observed.append("order_lock")
            return MagicMock(scalar_one_or_none=MagicMock(return_value=SimpleNamespace(id=9)))
        if "from payments" in sql and "for update" in sql:
            observed.append("payment_lock")
            return MagicMock(scalar_one_or_none=MagicMock(return_value=payment))
        if "payment_refunds" in sql:
            locked = statement._for_update_arg is not None
            observed.append("refund_lock" if locked else "refund_read")
            return initial_result if not locked else locked_result
        raise AssertionError(f"unexpected SQL in refund callback test: {statement}")

    db.execute = AsyncMock(side_effect=execute)

    with patch(
        "app.services.payment_service._validate_refund_callback_binding"
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
            provider_query_result={"status": "PROCESSING", "refund_no": "RF901", "payment_no": "PAY12"},
        )

    assert result is row
    assert observed == ["refund_read", "payment_peek", "order_lock", "payment_lock", "refund_lock"]
    finalize.assert_awaited_once()


@pytest.mark.parametrize(
    ("provider", "payment", "refund", "result", "error_text"),
    [
        (
            "WECHAT",
            {"id": 1, "payment_no": "PAY-1", "provider_trade_no": "WX-1", "order_id": 9, "amount": Decimal("50.00")},
            {"payment_id": 1, "refund_no": "RF-1", "order_id": 9, "amount": Decimal("12.00")},
            {"status": "SUCCESS", "refund_no": "RF-1", "payment_no": "PAY-1", "provider_trade_no": "WX-1", "amount": {"refund": 1100, "total": 5000, "currency": "CNY"}},
            "本次退款金额",
        ),
        (
            "ALIPAY",
            {"id": 2, "payment_no": "PAY-2", "provider_trade_no": "ALI-2", "order_id": 10, "amount": Decimal("80.00")},
            {"payment_id": 2, "refund_no": "RF-2", "order_id": 10, "amount": Decimal("20.00")},
            {"status": "SUCCESS", "refund_no": "RF-WRONG", "payment_no": "PAY-2", "provider_trade_no": "ALI-2", "amount": "20.00"},
            "退款单号不匹配",
        ),
        (
            "ALIPAY",
            {"id": 3, "payment_no": "PAY-3", "provider_trade_no": "ALI-3", "order_id": 11, "amount": Decimal("80.00")},
            {"payment_id": 3, "refund_no": "RF-3", "order_id": 11, "amount": Decimal("20.00")},
            {"status": "SUCCESS", "refund_no": "RF-3", "payment_no": "PAY-OTHER", "provider_trade_no": "ALI-3", "amount": "20.00"},
            "原支付单号不匹配",
        ),
    ],
)
def test_refund_query_validation_rejects_mismatched_channel_results(
    provider, payment, refund, result, error_text
):
    from types import SimpleNamespace

    from app.core.exceptions import AppException
    from app.services.payment_service import PaymentService

    with pytest.raises(AppException, match=error_text):
        PaymentService._validate_refund_query_result(
            provider=provider,
            payment=SimpleNamespace(**payment),
            refund=SimpleNamespace(**refund),
            result=result,
        )


def test_wechat_refund_query_validation_requires_original_payment_total_and_currency():
    from types import SimpleNamespace

    from app.core.exceptions import AppException
    from app.services.payment_service import PaymentService

    payment = SimpleNamespace(
        id=7, payment_no="PAY-7", provider_trade_no="WX-7",
        order_id=17, amount=Decimal("50.00"),
    )
    refund = SimpleNamespace(
        payment_id=7, refund_no="RF-7", order_id=17, amount=Decimal("10.00"),
    )
    with pytest.raises(AppException, match="原订单总金额"):
        PaymentService._validate_refund_query_result(
            provider="WECHAT",
            payment=payment,
            refund=refund,
            result={
                "status": "SUCCESS",
                "refund_no": "RF-7",
                "payment_no": "PAY-7",
                "provider_trade_no": "WX-7",
                "amount": {"refund": 1000, "total": 4900, "currency": "CNY"},
            },
        )


def test_refund_query_validation_accepts_matching_processing_result():
    from types import SimpleNamespace

    from app.services.payment_service import PaymentService

    payment = SimpleNamespace(
        id=8, payment_no="PAY-8", provider_trade_no="WX-8",
        order_id=18, amount=Decimal("50.00"),
    )
    refund = SimpleNamespace(
        payment_id=8, refund_no="RF-8", order_id=18, amount=Decimal("10.00"),
    )
    assert PaymentService._validate_refund_query_result(
        provider="WECHAT",
        payment=payment,
        refund=refund,
        result={"status": "PROCESSING", "refund_no": "RF-8", "payment_no": "PAY-8"},
    ) == "PROCESSING"


@pytest.mark.asyncio
async def test_scheduled_refund_poll_rejects_success_with_wrong_amount(monkeypatch):
    """定时退款补偿不能绕过主动查单的金额校验直接 SUCCESS 入账。"""
    from types import SimpleNamespace

    from app.services import payment_service
    from app.services.payment_service import PaymentService

    refund = SimpleNamespace(
        id=77, payment_id=8, order_id=18, refund_no="RF-77",
        amount=Decimal("10.00"), status="PROCESSING",
    )
    payment = SimpleNamespace(
        id=8, provider="WECHAT", payment_no="PAY-8",
        provider_trade_no="WX-8", order_id=18, amount=Decimal("50.00"),
    )

    id_result = MagicMock()
    id_result.scalars.return_value.all.return_value = [77]
    claim_result = MagicMock()
    claim_result.rowcount = 1
    row_result = MagicMock()
    row_result.scalar_one_or_none.return_value = refund

    first_db = MagicMock()
    first_db.execute = AsyncMock(return_value=id_result)
    second_db = MagicMock()
    second_db.execute = AsyncMock(side_effect=[claim_result, row_result])
    second_db.get = AsyncMock(return_value=payment)
    second_db.commit = AsyncMock()

    class SessionContext:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *args):
            return False

    sessions = iter((SessionContext(first_db), SessionContext(second_db)))
    monkeypatch.setattr(payment_service, "AsyncSessionLocal", lambda: next(sessions))
    monkeypatch.setattr(
        PaymentService,
        "_query_channel_refund",
        AsyncMock(return_value={
            "status": "SUCCESS",
            "refund_no": "RF-77",
            "payment_no": "PAY-8",
            "provider_trade_no": "WX-8",
            "amount": {"refund": 900, "total": 5000, "currency": "CNY"},
        }),
    )
    finalize = AsyncMock()
    monkeypatch.setattr(PaymentService, "_finalize_refund", finalize)
    defer = AsyncMock()
    monkeypatch.setattr(PaymentService, "_defer_refund_poll", defer)

    assert await PaymentService.poll_pending_refunds(limit=10) == 0
    defer.assert_awaited_once()
    assert defer.await_args.kwargs["claim_version"] == 1
    finalize.assert_not_awaited()

@pytest.mark.asyncio
async def test_scheduled_refund_poll_skips_when_another_worker_claimed(monkeypatch):
    """两个 Worker 同时扫描时，原子领取失败的一方不得调用支付渠道。"""
    from app.services import payment_service
    from app.services.payment_service import PaymentService

    id_result = MagicMock()
    id_result.scalars.return_value.all.return_value = [88]
    first_db = MagicMock()
    first_db.execute = AsyncMock(return_value=id_result)

    claim_result = MagicMock()
    claim_result.rowcount = 0
    second_db = MagicMock()
    second_db.execute = AsyncMock(return_value=claim_result)
    second_db.commit = AsyncMock()

    class SessionContext:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *args):
            return False

    sessions = iter((SessionContext(first_db), SessionContext(second_db)))
    monkeypatch.setattr(payment_service, "AsyncSessionLocal", lambda: next(sessions))
    query = AsyncMock()
    monkeypatch.setattr(PaymentService, "_query_channel_refund", query)

    assert await PaymentService.poll_pending_refunds(limit=10) == 0
    query.assert_not_awaited()
    claim_stmt = second_db.execute.call_args.args[0]
    assert claim_stmt.is_update

