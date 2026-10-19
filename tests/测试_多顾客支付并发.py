"""多顾客高并发支付：隔离与查单锁外查询回归。"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payment_service import PaymentService


@pytest.mark.asyncio
async def test_lock_order_then_payment_orders_locks():
    """统一锁序：先 Order 再 Payment（降低与关单路径死锁）。"""
    calls: list[str] = []

    async def execute(stmt):
        sql = str(stmt).lower()
        calls.append(sql)
        res = MagicMock()
        if "payments" in sql and "order_id" in sql and "for update" not in sql:
            res.first.return_value = (9, None)  # order_id, recharge_id
            return res
        if "orders" in sql:
            res.scalar_one_or_none.return_value = SimpleNamespace(id=9)
            return res
        if "payments" in sql:
            res.scalar_one_or_none.return_value = SimpleNamespace(id=1, order_id=9)
            return res
        res.first.return_value = None
        res.scalar_one_or_none.return_value = None
        return res

    db = AsyncMock()
    db.execute = execute
    out = await PaymentService._lock_order_then_payment(db, 1)
    assert out is not None
    order_i = next(i for i, s in enumerate(calls) if "orders" in s and "for update" in s)
    pay_fus = [i for i, s in enumerate(calls) if "payments" in s and "for update" in s]
    assert pay_fus and order_i < pay_fus[0]


@pytest.mark.asyncio
async def test_query_channel_trade_no_db():
    """渠道查单不碰 DB，可在行锁外并行调用。"""
    payment = SimpleNamespace(
        payment_no="P1",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("10.00"),
        status="PENDING",
    )
    with patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service._wechat_micropay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.query_by_out_trade_no",
        new_callable=AsyncMock,
        return_value={
            "trade_state": "SUCCESS",
            "transaction_id": "tx1",
            "amount_total_fen": 1000,
            "raw": {},
        },
    ):
        ch = await PaymentService._query_channel_trade(payment)
    assert ch["paid"] is True
    assert ch["provider"] == "WECHAT"


@pytest.mark.asyncio
async def test_poll_queries_channel_outside_row_lock():
    """poll：先释放行锁再调渠道，避免锁住多顾客支付行做 HTTP。"""
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 19, 12, 0, 0)
    payment = SimpleNamespace(
        id=7,
        payment_no="PAY7",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("5.00"),
        order_id=3,
        recharge_order_id=None,
        created_at=now - timedelta(minutes=2),
        updated_at=now - timedelta(minutes=2),
    )

    sessions: list = []

    class _CM:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *a):
            return False

    id_scalars = MagicMock()
    id_scalars.all.return_value = [7]
    id_db = AsyncMock()
    id_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=id_scalars))
    )

    claim_db = AsyncMock()
    claim_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=payment))
    )
    claim_db.commit = AsyncMock()

    settle_payment = SimpleNamespace(
        id=7,
        payment_no="PAY7",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("5.00"),
        order_id=3,
        recharge_order_id=None,
    )

    async def _settle(_db, pay, **_kw):
        pay.status = "PAID"

    settle_db = AsyncMock()
    settle_db.commit = AsyncMock()
    settle_db.get = AsyncMock(return_value=SimpleNamespace(order_no="O3", store_id=1))

    dbs = [id_db, claim_db, settle_db]

    def _session_local():
        db = dbs[len(sessions)] if len(sessions) < len(dbs) else settle_db
        sessions.append(db)
        return _CM(db)

    channel = {
        "paid": True,
        "provider": "WECHAT",
        "provider_trade_no": "tx",
        "trade_state": "SUCCESS",
        "query_raw": {},
    }
    query_mock = AsyncMock(return_value=channel)
    settle_mock = AsyncMock(side_effect=_settle)
    lock_mock = AsyncMock(return_value=settle_payment)

    with patch(
        "app.services.payment_service.AsyncSessionLocal", side_effect=_session_local
    ), patch(
        "app.core.response.naive_local_now", return_value=now
    ), patch.object(
        PaymentService, "_query_channel_trade", query_mock
    ), patch.object(
        PaymentService, "_lock_order_then_payment", lock_mock
    ), patch.object(
        PaymentService, "_settle_from_query", settle_mock
    ), patch(
        "app.services.websocket_manager.ws_manager.notify_order_event",
        new_callable=AsyncMock,
    ):
        n = await PaymentService.poll_pending_payments(limit=10, min_age_seconds=0)

    assert n == 1
    # 查单在 claim commit 之后（锁已释放）
    claim_db.commit.assert_awaited()
    query_mock.assert_awaited()
    settle_mock.assert_awaited()
    assert query_mock.await_args.args[0].payment_no == "PAY7"
