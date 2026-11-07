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


@pytest.mark.asyncio
async def test_poll_refunds_queries_channel_outside_row_lock():
    """退款 poll：渠道查单在行锁外，入账短事务。"""
    refund = SimpleNamespace(
        id=11,
        refund_no="RF11",
        payment_id=3,
        order_id=9,
        amount=Decimal("10.00"),
        status="PROCESSING",
        retry_count=0,
    )
    payment = SimpleNamespace(
        id=3,
        payment_no="PAY3",
        provider="WECHAT",
        provider_trade_no="WX3",
        order_id=9,
        amount=Decimal("50.00"),
    )

    class _CM:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *a):
            return False

    id_scalars = MagicMock()
    id_scalars.all.return_value = [11]
    id_db = AsyncMock()
    id_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=id_scalars))
    )

    claim_db = AsyncMock()
    claim_result = MagicMock(rowcount=1)
    claimed_row_result = MagicMock(scalar_one_or_none=MagicMock(return_value=refund))
    claim_db.execute = AsyncMock(side_effect=[claim_result, claimed_row_result])
    claim_db.get = AsyncMock(return_value=payment)
    claim_db.commit = AsyncMock()

    settle_row = SimpleNamespace(
        id=11,
        refund_no="RF11",
        payment_id=3,
        order_id=9,
        amount=Decimal("10.00"),
        status="PROCESSING",
    )
    settle_order = SimpleNamespace(id=9)
    settle_payment = SimpleNamespace(
        id=3,
        payment_no="PAY3",
        provider="WECHAT",
        provider_trade_no="WX3",
        order_id=9,
        amount=Decimal("50.00"),
    )

    def _scalar_result(value):
        return MagicMock(scalar_one_or_none=MagicMock(return_value=value))

    async def _finalize(_db, row, status, **_kw):
        row.status = status

    settle_db = AsyncMock()
    # 依次模拟：读取支付关联、锁订单、锁支付、锁退款行；
    # SUCCESS 入账前再次校验并锁定订单与支付，顺序始终为 Order→Payment→Refund。
    payment_peek = MagicMock()
    payment_peek.first.return_value = (9, None)
    settle_db.execute = AsyncMock(
        side_effect=[
            payment_peek,
            _scalar_result(settle_order),
            _scalar_result(settle_payment),
            _scalar_result(settle_row),
            _scalar_result(settle_order),
            _scalar_result(settle_payment),
        ]
    )
    settle_db.commit = AsyncMock()
    settle_db.get = AsyncMock(
        return_value=SimpleNamespace(
            order_no="O9", status="REFUNDED", payment_status="REFUNDED", store_id=1
        )
    )

    dbs = [id_db, claim_db, settle_db]
    n = {"i": 0}

    def _session():
        db = dbs[min(n["i"], len(dbs) - 1)]
        n["i"] += 1
        return _CM(db)

    with patch(
        "app.services.payment_service.AsyncSessionLocal", side_effect=_session
    ), patch.object(
        PaymentService,
        "_query_channel_refund",
        new_callable=AsyncMock,
        return_value={
            "status": "SUCCESS",
            "refund_no": "RF11",
            "payment_no": "PAY3",
            "provider_trade_no": "WX3",
            "amount": {"refund": 1000, "total": 5000, "currency": "CNY"},
            "provider_refund_no": "wxr1",
            "raw": {},
        },
    ) as q, patch.object(
        PaymentService, "_finalize_refund", new_callable=AsyncMock, side_effect=_finalize
    ), patch(
        "app.services.websocket_manager.ws_manager.notify_order_event",
        new_callable=AsyncMock,
    ):
        got = await PaymentService.poll_pending_refunds(limit=10)

    assert got == 1
    claim_db.commit.assert_awaited()
    q.assert_awaited()
    assert q.await_args.kwargs["refund_no"] == "RF11"


@pytest.mark.asyncio
async def test_platform_token_refresh_http_outside_lock(monkeypatch):
    """Token 刷新：到期判断后释放行锁再 HTTP，再短锁写回。"""
    from datetime import datetime, timedelta

    from app.services.platform_token_service import PlatformTokenService

    binding = SimpleNamespace(
        id=1,
        platform="MEITUAN",
        status=1,
        refresh_token="enc_rt",
        access_token="enc_at",
        token_expires_at=datetime.now() - timedelta(hours=1),
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
    id_scalars.all.return_value = [1]
    id_db = AsyncMock()
    id_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=id_scalars))
    )

    claim_db = AsyncMock()
    claim_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=binding))
    )
    claim_db.commit = AsyncMock()

    write_db = AsyncMock()
    write_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=binding))
    )
    write_db.commit = AsyncMock()

    dbs = [id_db, claim_db, write_db]
    n = {"i": 0}

    def _session():
        db = dbs[min(n["i"], len(dbs) - 1)]
        n["i"] += 1
        sessions.append(db)
        return _CM(db)

    fetch = AsyncMock(
        return_value={"access_token": "new", "refresh_token": "nr", "expires_in": 3600}
    )
    apply = MagicMock()

    with patch(
        "app.services.platform_token_service.AsyncSessionLocal", side_effect=_session
    ), patch.object(
        PlatformTokenService, "_fetch_token_payload", fetch
    ), patch.object(
        PlatformTokenService, "_apply_token_payload", apply
    ):
        got = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
    assert got == 1
    claim_db.commit.assert_awaited()
    fetch.assert_awaited()
    apply.assert_called_once()
    write_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sync_payment_status_queries_before_row_lock():
    """sync_payment_status：先渠道查单，再 Order/Payment 行锁入账。"""
    payment = SimpleNamespace(
        id=5,
        payment_no="P5",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("8.00"),
    )
    locked = SimpleNamespace(
        id=5,
        payment_no="P5",
        status="PENDING",
        provider="WECHAT",
        channel="WECHAT_MINIPROGRAM",
        amount=Decimal("8.00"),
    )
    order_calls: list[str] = []

    async def _query(p):
        order_calls.append("query")
        return {
            "paid": True,
            "provider": "WECHAT",
            "provider_trade_no": "tx5",
            "trade_state": "SUCCESS",
            "query_raw": {},
        }

    async def _lock(_db, _pid):
        order_calls.append("lock")
        return locked

    async def _settle(_db, pay, **_kw):
        order_calls.append("settle")
        pay.status = "PAID"

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=payment))
    )

    with patch.object(PaymentService, "_query_channel_trade", side_effect=_query), patch.object(
        PaymentService, "_lock_order_then_payment", side_effect=_lock
    ), patch.object(PaymentService, "_settle_from_query", side_effect=_settle):
        row, meta = await PaymentService.sync_payment_status(db, payment)

    assert row.status == "PAID"
    assert meta.get("synced") is True
    assert order_calls == ["query", "lock", "settle"]


@pytest.mark.asyncio
async def test_close_channel_payments_by_id_http_outside_order_lock():
    """关单撤销渠道：HTTP 在独立会话外，不拖住订单行锁。"""
    order = SimpleNamespace(id=9, order_no="O9", payment_status="UNPAID")
    pay = SimpleNamespace(id=3, payment_no="PAY3", provider="WECHAT", status="PENDING")

    class _CM:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *a):
            return False

    peek_db = AsyncMock()
    peek_db.get = AsyncMock(return_value=order)
    peek_db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=iter([pay])))
    )
    peek_db.commit = AsyncMock()

    sync_db = AsyncMock()
    sync_db.get = AsyncMock(return_value=pay)
    sync_db.commit = AsyncMock()

    close_db = AsyncMock()
    close_db.commit = AsyncMock()

    dbs = [peek_db, sync_db, close_db]
    n = {"i": 0}

    def _session():
        db = dbs[min(n["i"], len(dbs) - 1)]
        n["i"] += 1
        return _CM(db)

    close_http = AsyncMock()
    sync_mock = AsyncMock(return_value=(pay, {"synced": False}))
    lock_mock = AsyncMock(return_value=pay)
    close_local = AsyncMock()

    with patch(
        "app.services.payment_service.AsyncSessionLocal", side_effect=_session
    ), patch(
        "app.services.payment_service._wechat_pay_configured", return_value=True
    ), patch(
        "app.services.payment_service.wechat_pay_client.close_order", close_http
    ), patch.object(
        PaymentService, "sync_payment_status", sync_mock
    ), patch.object(
        PaymentService, "_lock_order_then_payment", lock_mock
    ), patch.object(
        PaymentService, "_close_pending_payment", close_local
    ):
        late = await PaymentService.close_channel_payments_by_id(9)

    assert late is False
    peek_db.commit.assert_awaited()
    close_http.assert_awaited_once()
    assert close_http.await_args.kwargs["payment_no"] == "PAY3"
    close_local.assert_awaited()
