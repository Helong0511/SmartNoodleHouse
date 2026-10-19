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
