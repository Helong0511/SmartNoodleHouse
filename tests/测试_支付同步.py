"""支付查单补偿 / 复用未支付单相关单测。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.payment_service import PaymentService
from app.services.wechat_pay_client import build_jsapi_pay_params


def test_build_jsapi_pay_params_requires_prepay_id():
    with pytest.raises(AppException):
        build_jsapi_pay_params(payment_no="P1", prepay_id="")


def test_build_jsapi_pay_params_signs(monkeypatch):
    monkeypatch.setattr(
        "app.services.wechat_pay_client.get_settings",
        lambda: SimpleNamespace(wechat_app_id="wx_app"),
    )
    monkeypatch.setattr(
        "app.services.wechat_pay_client._sign_message",
        lambda msg: "SIG",
    )
    data = build_jsapi_pay_params(payment_no="PAY1", prepay_id="prepay_abc")
    assert data["package"] == "prepay_id=prepay_abc"
    assert data["paySign"] == "SIG"
    assert data["payment_no"] == "PAY1"


def test_rebuild_client_pay_params_wechat(monkeypatch):
    monkeypatch.setattr(
        "app.services.payment_service.wechat_pay_client.build_jsapi_pay_params",
        lambda **kwargs: {
            "provider": "WECHAT",
            "payment_no": kwargs["payment_no"],
            "prepay_id": kwargs["prepay_id"],
            "timeStamp": "1",
            "nonceStr": "n",
            "package": f"prepay_id={kwargs['prepay_id']}",
            "signType": "RSA",
            "paySign": "s",
        },
    )
    payment = SimpleNamespace(
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        provider_order_no="prepay_x",
        payment_no="PAYX",
        amount=Decimal("12.00"),
    )
    data = PaymentService._rebuild_client_pay_params(payment)
    assert data["prepay_id"] == "prepay_x"
    assert data["reused"] is True


def test_rebuild_client_pay_params_alipay():
    payment = SimpleNamespace(
        provider="ALIPAY",
        channel="ALIPAY_MINIAPP",
        provider_order_no="2026000111",
        payment_no="PAYA",
        amount=Decimal("9.90"),
    )
    data = PaymentService._rebuild_client_pay_params(payment)
    assert data["trade_no"] == "2026000111"
    assert data["reused"] is True


def test_stash_client_pay_ref():
    payment = SimpleNamespace(provider_order_no=None)
    PaymentService._stash_client_pay_ref(payment, {"prepay_id": "p1", "_mock": False})
    assert payment.provider_order_no == "p1"
    payment2 = SimpleNamespace(provider_order_no=None)
    PaymentService._stash_client_pay_ref(payment2, {"trade_no": "t1"})
    assert payment2.provider_order_no == "t1"
    payment3 = SimpleNamespace(provider_order_no=None)
    PaymentService._stash_client_pay_ref(payment3, {"prepay_id": "p2", "_mock": True})
    assert payment3.provider_order_no is None


@pytest.mark.asyncio
async def test_sync_payment_status_wechat_success():
    payment = SimpleNamespace(
        id=1,
        payment_no="PAY_SYNC",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("10.00"),
        order_id=9,
        recharge_order_id=None,
        provider_trade_no=None,
        paid_at=None,
        callback_raw=None,
    )
    order = SimpleNamespace(
        id=9,
        order_no="O1",
        status="PENDING_PAYMENT",
        payment_status="UNPAID",
        total_amount=Decimal("10.00"),
    )

    db = AsyncMock()
    # 1) peek Payment  2) settle 内再锁 Order
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=payment)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=order)),
        ]
    )
    db.flush = AsyncMock()

    with patch.object(
        PaymentService,
        "_lock_order_then_payment",
        new_callable=AsyncMock,
        return_value=payment,
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service._wechat_micropay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.query_by_out_trade_no",
        new_callable=AsyncMock,
        return_value={
            "trade_state": "SUCCESS",
            "transaction_id": "wx_tx_1",
            "amount_total_fen": 1000,
            "raw": {},
        },
    ), patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ), patch.object(
        PaymentService,
        "_finalize_tender",
        new_callable=AsyncMock,
        return_value={"fully_paid": True},
    ):
        row, meta = await PaymentService.sync_payment_status(db, payment)

    assert row.status == "PAID"
    assert meta["synced"] is True
    assert row.provider_trade_no == "wx_tx_1"


@pytest.mark.asyncio
async def test_sync_payment_status_already_paid():
    payment = SimpleNamespace(
        id=2,
        payment_no="PAY_DONE",
        status="PAID",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("1.00"),
        order_id=1,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=payment))
    )
    row, meta = await PaymentService.sync_payment_status(db, payment)
    assert meta["already_paid"] is True
    assert row.status == "PAID"


@pytest.mark.asyncio
async def test_sync_closed_payment_resurrects_on_channel_success():
    """本地 CLOSED 仍查渠道；SUCCESS 时可 CLOSED→PAID。"""
    payment = SimpleNamespace(
        id=3,
        payment_no="PAY_CLOSED",
        status="CLOSED",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("10.00"),
        order_id=9,
        recharge_order_id=None,
        provider_trade_no=None,
        paid_at=None,
        callback_raw=None,
    )
    order = SimpleNamespace(
        id=9,
        order_no="O_CLOSED",
        status="CANCELLED",
        payment_status="CANCELLED",
        total_amount=Decimal("10.00"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=payment)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=order)),
        ]
    )
    db.flush = AsyncMock()

    with patch.object(
        PaymentService,
        "_lock_order_then_payment",
        new_callable=AsyncMock,
        return_value=payment,
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service._wechat_micropay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.query_by_out_trade_no",
        new_callable=AsyncMock,
        return_value={
            "trade_state": "SUCCESS",
            "transaction_id": "wx_late_1",
            "amount_total_fen": 1000,
            "raw": {},
        },
    ), patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ), patch.object(
        PaymentService,
        "_finalize_tender",
        new_callable=AsyncMock,
        return_value={"fully_paid": True},
    ):
        row, meta = await PaymentService.sync_payment_status(db, payment)

    assert row.status == "PAID"
    assert meta["synced"] is True
    assert row.provider_trade_no == "wx_late_1"


@pytest.mark.asyncio
async def test_poll_pending_payments_settles():
    """poll：锁外查渠道 + 短事务入账（适配多顾客并发）。"""
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 19, 12, 0, 0)
    payment = SimpleNamespace(
        id=3,
        payment_no="PAY_POLL",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("5.00"),
        order_id=7,
        recharge_order_id=None,
        created_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=5),
    )

    class _CM:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *a):
            return False

    id_scalars = MagicMock()
    id_scalars.all.return_value = [3]
    id_db = AsyncMock()
    id_db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=id_scalars)))

    claim_db = AsyncMock()
    claim_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=payment))
    )
    claim_db.commit = AsyncMock()

    settle_payment = SimpleNamespace(
        id=3,
        payment_no="PAY_POLL",
        status="PENDING",
        order_id=7,
    )

    async def _settle(_db, pay, **_kw):
        pay.status = "PAID"

    settle_db = AsyncMock()
    settle_db.commit = AsyncMock()
    settle_db.get = AsyncMock(return_value=SimpleNamespace(order_no="O7", store_id=1))

    dbs = [id_db, claim_db, settle_db]
    n_sess = {"i": 0}

    def _session():
        db = dbs[min(n_sess["i"], len(dbs) - 1)]
        n_sess["i"] += 1
        return _CM(db)

    with patch(
        "app.services.payment_service.AsyncSessionLocal", side_effect=_session
    ), patch(
        "app.core.response.naive_local_now", return_value=now
    ), patch.object(
        PaymentService,
        "_query_channel_trade",
        new_callable=AsyncMock,
        return_value={
            "paid": True,
            "provider": "WECHAT",
            "provider_trade_no": "tx",
            "trade_state": "SUCCESS",
            "query_raw": {},
        },
    ), patch.object(
        PaymentService,
        "_lock_order_then_payment",
        new_callable=AsyncMock,
        return_value=settle_payment,
    ), patch.object(
        PaymentService, "_settle_from_query", new_callable=AsyncMock, side_effect=_settle
    ), patch(
        "app.services.websocket_manager.ws_manager.notify_order_event",
        new_callable=AsyncMock,
    ):
        n = await PaymentService.poll_pending_payments(limit=10, min_age_seconds=0)

    assert n == 1
    claim_db.commit.assert_awaited()
    settle_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sync_refund_status并发回调已成功时不得重复执行入账(monkeypatch):
    """平台查单在锁外；期间若回调先成功，必须以锁内当前行状态为准。"""
    payment = SimpleNamespace(
        id=31, payment_no="PAY_RF_RACE", provider="ALIPAY",
        provider_trade_no="ali_trade_31", order_id=41,
        amount=Decimal("20.00"),
    )
    stale = SimpleNamespace(
        id=51, refund_no="RF_RACE", payment_id=31, order_id=41,
        amount=Decimal("5.00"), status="PROCESSING",
    )
    current = SimpleNamespace(
        id=51, refund_no="RF_RACE", payment_id=31, order_id=41,
        amount=Decimal("5.00"), status="SUCCESS",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = current
    db = AsyncMock()
    db.get = AsyncMock(return_value=payment)
    db.execute = AsyncMock(return_value=result)

    with patch.object(
        PaymentService, "_query_channel_refund", new_callable=AsyncMock,
        return_value={
            "status": "SUCCESS", "refund_no": "RF_RACE",
            "payment_no": "PAY_RF_RACE", "provider_trade_no": "ali_trade_31",
            "amount": "5.00", "provider_refund_no": "ali_refund_51",
        },
    ), patch.object(
        PaymentService, "_finalize_refund", new_callable=AsyncMock
    ) as finalize:
        returned = await PaymentService.sync_refund_status(db, stale)

    assert returned is current
    finalize.assert_not_awaited()
    stmt = db.execute.await_args.args[0]
    assert stmt._for_update_arg is not None


@pytest.mark.asyncio
async def test_sync_refund_status使用查单后锁内刷新出来的最新退款行(monkeypatch):
    payment = SimpleNamespace(
        id=32, payment_no="PAY_RF_REFRESH", provider="ALIPAY",
        provider_trade_no="ali_trade_32", order_id=42,
        amount=Decimal("30.00"),
    )
    stale = SimpleNamespace(
        id=52, refund_no="RF_REFRESH", payment_id=32, order_id=42,
        amount=Decimal("7.00"), status="UNKNOWN",
    )
    current = SimpleNamespace(
        id=52, refund_no="RF_REFRESH", payment_id=32, order_id=42,
        amount=Decimal("7.00"), status="PROCESSING",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = current
    db = AsyncMock()
    db.get = AsyncMock(return_value=payment)
    db.execute = AsyncMock(return_value=result)

    with patch.object(
        PaymentService, "_query_channel_refund", new_callable=AsyncMock,
        return_value={
            "status": "PROCESSING", "refund_no": "RF_REFRESH",
            "payment_no": "PAY_RF_REFRESH", "provider_trade_no": "ali_trade_32",
        },
    ), patch.object(
        PaymentService, "_finalize_refund", new_callable=AsyncMock,
        return_value=current,
    ) as finalize:
        returned = await PaymentService.sync_refund_status(db, stale)

    assert returned is current
    finalize.assert_awaited_once()
    assert finalize.await_args.args[1] is current
    assert finalize.await_args.args[2] == "PROCESSING"
    stmt = db.execute.await_args.args[0]
    assert stmt._for_update_arg is not None


@pytest.mark.asyncio
async def test_refund_idempotency_key_retry_returns_original_after_full_refund(monkeypatch):
    payment = SimpleNamespace(id=71, status="REFUNDED")
    existing = SimpleNamespace(
        id=81, payment_id=71, amount=Decimal("6.00"),
        reason="顾客取消", status="SUCCESS", idempotency_key="refund-retry-81",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=payment))

    returned = await PaymentService.refund(
        db, payment, Decimal("6.00"), "顾客取消",
        idempotency_key="refund-retry-81",
    )

    assert returned is existing
    assert getattr(returned, "_idempotent_replay", False) is True
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_refund_idempotency_key_cannot_be_reused_with_different_amount(monkeypatch):
    payment = SimpleNamespace(id=72, status="PARTIAL_REFUND")
    existing = SimpleNamespace(
        id=82, payment_id=72, amount=Decimal("6.00"),
        reason="顾客取消", status="SUCCESS", idempotency_key="refund-retry-82",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=payment))

    with pytest.raises(AppException) as exc:
        await PaymentService.refund(
            db, payment, Decimal("8.00"), "顾客取消",
            idempotency_key="refund-retry-82",
        )

    assert exc.value.code == 40955
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_refund_idempotency_key_cannot_cross_payment_boundary(monkeypatch):
    payment = SimpleNamespace(id=73, status="PAID")
    existing = SimpleNamespace(
        id=83, payment_id=999, amount=Decimal("6.00"),
        reason="顾客取消", status="SUCCESS", idempotency_key="refund-retry-83",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=payment))

    with pytest.raises(AppException) as exc:
        await PaymentService.refund(
            db, payment, Decimal("6.00"), "顾客取消",
            idempotency_key="refund-retry-83",
        )

    assert exc.value.code == 40955


@pytest.mark.asyncio
async def test_refund_api_idempotent_replay_does_not_duplicate_audit_or_websocket():
    from app.api.payments import RefundCreate, refund_payment

    payment = SimpleNamespace(id=91, payment_no="PAY_REPLAY", order_id=92)
    order = SimpleNamespace(
        id=92, order_no="ORDER_REPLAY", channel="ONLINE", store_id=3
    )
    refund = SimpleNamespace(
        id=93, refund_no="RF_REPLAY", status="SUCCESS",
        amount=Decimal("4.00"), _idempotent_replay=True,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = payment
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.get = AsyncMock(return_value=order)
    user = SimpleNamespace(id=7, role=SimpleNamespace(code="STORE_ADMIN"))
    request = MagicMock()

    with patch("app.api.payments.assert_order_store_access"), patch(
        "app.api.payments.PaymentService.refund",
        new_callable=AsyncMock,
        return_value=refund,
    ), patch(
        "app.services.audit_service.AuditService.log_refund",
        new_callable=AsyncMock,
    ) as audit, patch(
        "app.api.payments.ws_manager.notify_order_event",
        new_callable=AsyncMock,
    ) as notify:
        response = await refund_payment(
            "PAY_REPLAY",
            RefundCreate(amount=Decimal("4.00"), reason="顾客取消"),
            request,
            db,
            user,
            idempotency_key="stable-replay-key",
        )

    assert response is not None
    audit.assert_not_awaited()
    notify.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refund_idempotency_unique_race_returns_winning_refund(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    payment = SimpleNamespace(
        id=74, status="PAID", amount=Decimal("10.00"),
        provider="WECHAT", order_id=75, payment_no="PAY_RACE",
    )
    order = SimpleNamespace(id=75)
    winner = SimpleNamespace(
        id=84, payment_id=74, amount=Decimal("3.00"),
        reason="顾客取消", status="PENDING",
        idempotency_key="refund-race-key",
    )
    locked_result = MagicMock()
    locked_result.scalar_one_or_none.return_value = payment
    no_existing_result = MagicMock()
    no_existing_result.scalar_one_or_none.return_value = None
    winner_result = MagicMock()
    winner_result.scalar_one_or_none.return_value = winner

    db = AsyncMock()
    # AsyncSession.add() is synchronous; keep it a normal mock to avoid an
    # un-awaited coroutine warning that can hide real async lifecycle bugs.
    db.add = MagicMock()
    db.execute = AsyncMock(side_effect=[
        no_existing_result, winner_result,
    ])
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=payment))
    db.scalar = AsyncMock(side_effect=[0, Decimal("0.00")])
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock(
        side_effect=IntegrityError("INSERT payment_refunds", {}, Exception("duplicate"))
    )

    monkeypatch.setattr(
        "app.services.payment_service._order_table_parts",
        AsyncMock(return_value=("TABLE", "T1")),
    )
    monkeypatch.setattr(
        "app.services.payment_service._order_store_no",
        AsyncMock(return_value="S1"),
    )
    monkeypatch.setattr(
        "app.services.payment_service.generate_no",
        lambda *args, **kwargs: "RF_RACE",
    )

    returned = await PaymentService.refund(
        db, payment, Decimal("3.00"), "顾客取消",
        idempotency_key="refund-race-key",
    )

    assert returned is winner
    assert getattr(returned, "_idempotent_replay", False) is True
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_pos_refund_rejects_stale_execution_epoch_before_creating_refund():
    """旧审批 worker 即使恢复，也不能越过 DB fencing 创建渠道退款单。"""
    payment = SimpleNamespace(id=301, order_id=401, status="PAID", amount=Decimal("10.00"))
    request = SimpleNamespace(
        id=501,
        order_id=401,
        status="PROCESSING",
        execution_token="new-owner-token",
        execution_epoch=12,
        execution_lease_until=None,
        refund_id=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = request
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()

    with patch.object(
        PaymentService, "_lock_order_then_payment", new_callable=AsyncMock, return_value=payment
    ):
        with pytest.raises(AppException) as exc:
            await PaymentService.refund(
                db,
                payment,
                Decimal("10.00"),
                "POS 退款",
                pos_refund_request_id=501,
                pos_refund_execution_token="old-owner-token",
                pos_refund_execution_epoch=11,
            )

    assert exc.value.code == 40954
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_payment_sync_cooldown_returns_503_when_redis_fails(monkeypatch):
    from app.api import payments as payment_api

    async def redis_unavailable():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(payment_api, "get_redis", redis_unavailable)
    with pytest.raises(AppException) as exc:
        await payment_api._acquire_sync_cooldown(123)
    assert exc.value.code == 50301
    assert exc.value.status_code == 503
