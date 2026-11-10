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
    monkeypatch.setattr(PaymentService, "_refund_rows_current", AsyncMock(return_value=[]))
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


@pytest.mark.asyncio
async def test_lock_order_then_payment_refreshes_locked_payment_from_database():
    payment = SimpleNamespace(id=88, order_id=99, recharge_order_id=None, status="REFUNDED")
    peek_result = MagicMock()
    peek_result.first.return_value = (99, None)
    order_result = MagicMock()
    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[peek_result, order_result, payment_result])

    locked = await PaymentService._lock_order_then_payment(db, 88)

    assert locked is payment
    assert db.execute.await_count == 3
    order_stmt = db.execute.await_args_list[1].args[0]
    payment_stmt = db.execute.await_args_list[2].args[0]
    assert order_stmt._for_update_arg is not None
    assert order_stmt.get_execution_options().get("populate_existing") is True
    assert payment_stmt._for_update_arg is not None
    assert payment_stmt.get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
async def test_refund_creation_blocks_inflight_refund_from_current_locked_rows(monkeypatch):
    payment = SimpleNamespace(
        id=89, status="PAID", amount=Decimal("10.00"), order_id=90,
        provider="WECHAT", payment_no="PAY_CURRENT_READ",
    )
    inflight = SimpleNamespace(status="UNKNOWN", amount=Decimal("4.00"))
    load_rows = AsyncMock(return_value=[inflight])
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=payment))
    monkeypatch.setattr(PaymentService, "_refund_rows_current", load_rows)
    db = AsyncMock()

    with pytest.raises(AppException) as exc:
        await PaymentService.refund(db, payment, Decimal("1.00"), "retry")

    assert exc.value.code == 40054
    load_rows.assert_awaited_once_with(db, 89)
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_remaining_amount_can_use_current_locked_payment_and_refund_facts(monkeypatch):
    order = SimpleNamespace(
        id=91, total_amount=Decimal("20.00"), payment_status="PARTIAL_PAID", status="PENDING_PAYMENT"
    )
    paid = AsyncMock(return_value=Decimal("7.00"))
    db = AsyncMock()
    monkeypatch.setattr(PaymentService, "paid_amount", paid)

    remaining = await PaymentService.remaining_amount(db, order, current_read=True)

    assert remaining == Decimal("13.00")
    paid.assert_awaited_once_with(db, 91, current_read=True)


@pytest.mark.parametrize("settled_status", ["PAID", "PARTIAL_REFUND", "REFUNDED"])
@pytest.mark.asyncio
async def test_immediate_settlement_does_not_downgrade_terminal_callback_state(monkeypatch, settled_status):
    stale_payment = SimpleNamespace(id=101, order_id=102, status="PENDING")
    current_payment = SimpleNamespace(id=101, order_id=102, status=settled_status)
    current_order = SimpleNamespace(id=102, status="PAID", order_no="O102")
    lock = AsyncMock(return_value=current_payment)
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", lock)
    order_result = MagicMock()
    order_result.scalar_one_or_none.return_value = current_order
    db = AsyncMock()
    db.execute = AsyncMock(return_value=order_result)

    with patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ) as record, patch.object(
        PaymentService, "_finalize_tender", new_callable=AsyncMock
    ) as finalize:
        await PaymentService._settle_immediate_payment(
            db, stale_payment, SimpleNamespace(id=102), "WECHAT", "wx_trade_102"
        )

    lock.assert_awaited_once_with(db, 101)
    record.assert_not_awaited()
    finalize.assert_not_awaited()
    assert stale_payment.status == "PENDING"
    assert current_payment.status == settled_status
    stmt = db.execute.await_args.args[0]
    assert stmt._for_update_arg is not None
    assert stmt.get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
async def test_immediate_amount_mismatch_does_not_downgrade_concurrent_paid_payment(monkeypatch):
    current = SimpleNamespace(id=111, status="PAID")
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=current))
    db = AsyncMock()

    with patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ) as history:
        returned = await PaymentService._mark_order_payment_unknown_after_amount_mismatch(db, 111)

    assert returned is current
    assert current.status == "PAID"
    history.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_immediate_amount_mismatch_persists_unknown_and_status_history(monkeypatch):
    current = SimpleNamespace(id=112, status="PENDING")
    monkeypatch.setattr(PaymentService, "_lock_order_then_payment", AsyncMock(return_value=current))
    db = AsyncMock()

    with patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ) as history:
        returned = await PaymentService._mark_order_payment_unknown_after_amount_mismatch(db, 112)

    assert returned.status == "UNKNOWN"
    history.assert_awaited_once()
    assert history.await_args.kwargs["from_status"] == "PENDING"
    assert history.await_args.kwargs["to_status"] == "UNKNOWN"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recharge_amount_mismatch_uses_recharge_then_payment_current_locks():
    recharge = SimpleNamespace(id=113, status="PENDING")
    payment = SimpleNamespace(id=114, status="PENDING")
    recharge_result = MagicMock()
    recharge_result.scalar_one_or_none.return_value = recharge
    payment_result = MagicMock()
    payment_result.scalar_one_or_none.return_value = payment
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[recharge_result, payment_result])

    with patch(
        "app.services.payment_service.record_payment_status", new_callable=AsyncMock
    ) as history:
        returned = await PaymentService._mark_recharge_payment_unknown_after_amount_mismatch(db, 113, 114)

    assert returned is payment
    assert payment.status == "UNKNOWN"
    history.assert_awaited_once()
    assert db.execute.await_count == 2
    for call in db.execute.await_args_list:
        stmt = call.args[0]
        assert stmt._for_update_arg is not None
        assert stmt.get_execution_options().get("populate_existing") is True
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recharge_settlement_refreshes_paid_rows_before_duplicate_guard():
    stale_recharge = SimpleNamespace(id=121, status="PENDING")
    stale_payment = SimpleNamespace(id=122, status="PENDING")
    current_recharge = SimpleNamespace(id=121, status="PAID")
    current_payment = SimpleNamespace(id=122, status="PAID")
    recharge_result = MagicMock()
    recharge_result.scalar_one.return_value = current_recharge
    payment_result = MagicMock()
    payment_result.scalar_one.return_value = current_payment
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[recharge_result, payment_result])

    result = await PaymentService._settle_recharge_payment(
        db, payment=stale_payment, recharge=stale_recharge,
        provider="WECHAT", source="TEST_REPLAY",
    )

    assert result == {"already_paid": True, "status": "PAID"}
    assert db.execute.await_count == 2
    for call in db.execute.await_args_list:
        stmt = call.args[0]
        assert stmt._for_update_arg is not None
        assert stmt.get_execution_options().get("populate_existing") is True
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_recharge_settlement_never_downgrades_refunded_payment():
    stale_recharge = SimpleNamespace(id=123, status="PENDING")
    stale_payment = SimpleNamespace(id=124, status="PENDING")
    current_recharge = SimpleNamespace(id=123, status="PENDING")
    current_payment = SimpleNamespace(id=124, status="REFUNDED")
    recharge_result = MagicMock()
    recharge_result.scalar_one.return_value = current_recharge
    payment_result = MagicMock()
    payment_result.scalar_one.return_value = current_payment
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[recharge_result, payment_result])

    with pytest.raises(AppException) as exc:
        await PaymentService._settle_recharge_payment(
            db, payment=stale_payment, recharge=stale_recharge,
            provider="WECHAT", source="TEST_REPLAY",
        )

    assert exc.value.code == 50042
    assert current_payment.status == "REFUNDED"


@pytest.mark.parametrize(
    ("provider", "trade_state", "cancel_action", "expected"),
    [
        ("WECHAT", "CLOSED", None, True),
        ("WECHAT", "REVOKED", None, True),
        ("WECHAT", "PAYERROR", None, True),
        ("WECHAT", "NOTPAY", None, False),
        ("WECHAT", "USERPAYING", None, False),
        ("WECHAT", "UNKNOWN", None, False),
        ("ALIPAY", "TRADE_CLOSED", "close", True),
        ("ALIPAY", "TRADE_CLOSED", "refund", False),
        ("ALIPAY", "TRADE_CLOSED", None, False),
        ("ALIPAY", "WAIT_BUYER_PAY", "close", False),
        ("ALIPAY", "TRADE_SUCCESS", "close", False),
        ("OTHER", "CLOSED", None, False),
    ],
)
def test_channel_trade_must_be_explicit_terminal_unpaid(
    provider, trade_state, cancel_action, expected
):
    assert PaymentService._channel_trade_is_terminal_unpaid(
        provider, trade_state, cancel_action=cancel_action
    ) is expected


class _PaymentCloseSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_close_channel_payments_by_id_keeps_pending_when_close_and_query_are_uncertain():
    """渠道关单超时且查单 UNKNOWN 时，不能把本地支付单误关为 CLOSED。"""
    order = SimpleNamespace(id=701, order_no="O701", payment_status="UNPAID")
    payment = SimpleNamespace(
        id=702, payment_no="PAY702", provider="WECHAT",
        channel="WECHAT_MINIPROGRAM", status="PENDING",
    )
    rows = MagicMock()
    rows.scalars.return_value = [payment]
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[order, payment])
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    with patch(
        "app.services.payment_service.AsyncSessionLocal",
        side_effect=lambda: _PaymentCloseSessionContext(db),
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.close_order",
        new_callable=AsyncMock, side_effect=TimeoutError("provider timeout"),
    ), patch.object(
        PaymentService, "sync_payment_status", new_callable=AsyncMock,
        return_value=(payment, {"trade_state": "UNKNOWN"}),
    ), patch.object(
        PaymentService, "_lock_order_then_payment", new_callable=AsyncMock,
    ) as lock_payment, patch.object(
        PaymentService, "_close_pending_payment", new_callable=AsyncMock,
    ) as close_local:
        late_paid = await PaymentService.close_channel_payments_by_id(order.id)

    assert late_paid is False
    lock_payment.assert_not_awaited()
    close_local.assert_not_awaited()
    assert payment.status == "PENDING"


@pytest.mark.asyncio
async def test_close_channel_payments_by_id_keeps_pending_when_channel_query_raises():
    """关单接口失败后，查单本身异常也必须保留 PENDING。"""
    order = SimpleNamespace(id=711, order_no="O711", payment_status="UNPAID")
    payment = SimpleNamespace(
        id=712, payment_no="PAY712", provider="WECHAT",
        channel="WECHAT_MINIPROGRAM", status="PENDING",
    )
    rows = MagicMock()
    rows.scalars.return_value = [payment]
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[order, payment])
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    with patch(
        "app.services.payment_service.AsyncSessionLocal",
        side_effect=lambda: _PaymentCloseSessionContext(db),
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.close_order",
        new_callable=AsyncMock, side_effect=TimeoutError("provider timeout"),
    ), patch.object(
        PaymentService, "sync_payment_status", new_callable=AsyncMock,
        side_effect=TimeoutError("query timeout"),
    ), patch.object(
        PaymentService, "_lock_order_then_payment", new_callable=AsyncMock,
    ) as lock_payment, patch.object(
        PaymentService, "_close_pending_payment", new_callable=AsyncMock,
    ) as close_local:
        late_paid = await PaymentService.close_channel_payments_by_id(order.id)

    assert late_paid is False
    lock_payment.assert_not_awaited()
    close_local.assert_not_awaited()
    assert payment.status == "PENDING"


@pytest.mark.asyncio
async def test_close_channel_payments_keeps_pending_for_nonterminal_channel_state():
    """同步关单路径也必须等待渠道明确的未支付终态。"""
    order = SimpleNamespace(id=721, order_no="O721", payment_status="UNPAID")
    payment = SimpleNamespace(
        id=722, payment_no="PAY722", provider="WECHAT",
        channel="WECHAT_MINIPROGRAM", status="PENDING",
    )
    rows = MagicMock()
    rows.scalars.return_value = [payment]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=rows)

    with patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.close_order",
        new_callable=AsyncMock,
    ) as close_channel, patch.object(
        PaymentService, "sync_payment_status", new_callable=AsyncMock,
        return_value=(payment, {"trade_state": "USERPAYING"}),
    ), patch.object(
        PaymentService, "_close_pending_payment", new_callable=AsyncMock,
    ) as close_local:
        late_paid = await PaymentService.close_channel_payments(db, order)

    assert late_paid is False
    close_channel.assert_awaited_once_with(payment_no="PAY722")
    close_local.assert_not_awaited()
    assert payment.status == "PENDING"


@pytest.mark.asyncio
async def test_close_channel_payments_by_id_closes_only_after_terminal_unpaid_confirmation():
    """渠道明确返回 CLOSED 后才允许本地 PENDING 转 CLOSED。"""
    order = SimpleNamespace(id=731, order_no="O731", payment_status="UNPAID")
    payment = SimpleNamespace(
        id=732, payment_no="PAY732", provider="WECHAT",
        channel="WECHAT_MINIPROGRAM", status="PENDING",
    )
    rows = MagicMock()
    rows.scalars.return_value = [payment]
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[order, payment])
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    with patch(
        "app.services.payment_service.AsyncSessionLocal",
        side_effect=lambda: _PaymentCloseSessionContext(db),
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.close_order",
        new_callable=AsyncMock,
    ), patch.object(
        PaymentService, "sync_payment_status", new_callable=AsyncMock,
        return_value=(payment, {"trade_state": "CLOSED"}),
    ), patch.object(
        PaymentService, "_lock_order_then_payment", new_callable=AsyncMock,
        return_value=payment,
    ) as lock_payment, patch.object(
        PaymentService, "_close_pending_payment", new_callable=AsyncMock,
    ) as close_local:
        late_paid = await PaymentService.close_channel_payments_by_id(order.id)

    assert late_paid is False
    lock_payment.assert_awaited_once_with(db, payment.id)
    close_local.assert_awaited_once_with(
        db, payment, reason="超时关单同步撤销渠道交易"
    )


@pytest.mark.asyncio
async def test_close_channel_payments_by_id_does_not_treat_alipay_auto_refund_as_unpaid():
    """支付宝撤销 action=refund 表示已触发退款，TRADE_CLOSED 不可当作未付款关单。"""
    order = SimpleNamespace(id=741, order_no="O741", payment_status="UNPAID")
    payment = SimpleNamespace(
        id=742, payment_no="PAY742", provider="ALIPAY",
        channel="ALIPAY_MINIPROGRAM", status="PENDING",
    )
    rows = MagicMock()
    rows.scalars.return_value = [payment]
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[order, payment])
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    with patch(
        "app.services.payment_service.AsyncSessionLocal",
        side_effect=lambda: _PaymentCloseSessionContext(db),
    ), patch(
        "app.services.payment_service._alipay_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.alipay_client.trade_cancel",
        new_callable=AsyncMock, return_value={"action": "refund"},
    ), patch.object(
        PaymentService, "sync_payment_status", new_callable=AsyncMock,
        return_value=(payment, {"trade_state": "TRADE_CLOSED"}),
    ), patch.object(
        PaymentService, "_lock_order_then_payment", new_callable=AsyncMock,
    ) as lock_payment, patch.object(
        PaymentService, "_close_pending_payment", new_callable=AsyncMock,
    ) as close_local:
        late_paid = await PaymentService.close_channel_payments_by_id(order.id)

    assert late_paid is False
    lock_payment.assert_not_awaited()
    close_local.assert_not_awaited()
    assert payment.status == "PENDING"
