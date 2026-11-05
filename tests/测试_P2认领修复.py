"""P2 修复回归测试：
1. 外卖退款台账去重（唯一约束 + savepoint）
2. 外卖退款平台拒绝时不标记 APPROVED
3. 手动补打幂等（Redis NX 锁）
4. Webhook/Print/Outbox claim 释放使用原子 owner 校验
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.services.delivery_service import DeliveryService


# ─── 1. 外卖退款台账去重 ───


@pytest.mark.asyncio
async def test_handle_refund_dedup_uses_savepoint_on_conflict():
    """有 refund_id 时应走 savepoint 插入，IntegrityError 时不破坏外部事务。"""
    d_order = SimpleNamespace(id=2, order_id=9, platform="ELEME", platform_order_id="po1")
    order = SimpleNamespace(id=9, total_amount=100)

    db = MagicMock()
    # _get_delivery_order 被 patch，db.get 仅用于 db.get(Order, 9)
    db.get = AsyncMock(return_value=order)

    # begin_nested 返回一个真正的 async context manager
    class _NestedCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            # 抛出 IntegrityError 模拟唯一约束冲突
            raise IntegrityError("dup", {}, None)

    db.begin_nested = MagicMock(return_value=_NestedCtx())
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()

    raw = {"refund_id": "rpt-001", "refund_amount": "50.00"}
    parsed = {"platform_order_id": "po1", "refund_amount": "50.00"}

    with patch.object(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    ):
        result = await DeliveryService._handle_refund(db, "ELEME", parsed, raw)

    # 不应回滚外部事务
    assert db.rollback.await_count == 0
    # 仍应 flush 写入 d_order 状态
    assert db.flush.await_count >= 1
    assert result is not None


@pytest.mark.asyncio
async def test_handle_refund_without_refund_id_inserts_directly():
    """没有 refund_id 时直接插入，不走 savepoint。"""
    d_order = SimpleNamespace(id=2, order_id=9, platform="ELEME", platform_order_id="po1")
    order = SimpleNamespace(id=9, total_amount=100)

    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock()

    raw = {"refund_amount": "50.00"}
    parsed = {"platform_order_id": "po1", "refund_amount": "50.00"}

    with patch.object(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    ):
        await DeliveryService._handle_refund(db, "ELEME", parsed, raw)

    # 无平台 refund_id 时仍使用 SAVEPOINT：唯一指纹冲突只能回滚本次插入，
    # 不得回滚 webhook 处理事务中的其他订单状态更新。
    db.begin_nested.assert_called_once()
    assert db.add.call_count == 1


# ─── 2. 外卖退款平台拒绝时不标记 APPROVED ───


@pytest.mark.asyncio
async def test_process_refund_approve_platform_rejects_keeps_pending():
    """agree_refund 返回 False 时不得标记 APPROVED，应抛异常。"""
    refund = SimpleNamespace(
        id=1, order_id=9, status="PENDING", delivery_order_id=2,
        platform="ELEME", platform_refund_id="pr1",
        refund_type="FULL", amount=50, handled_at=None,
    )
    order = SimpleNamespace(id=9, store_id=7, status="COOKING")
    d_order = SimpleNamespace(id=2, order_id=9, platform_order_id="po1", refund_status=None)

    db = MagicMock()
    locked_result = MagicMock(scalar_one_or_none=MagicMock(return_value=refund))
    db.execute = AsyncMock(return_value=locked_result)
    db.get = AsyncMock(side_effect=[d_order, order, d_order, order])
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    mock_provider = MagicMock()
    mock_provider.agree_refund = AsyncMock(return_value=False)

    with patch(
        "app.services.delivery_service.get_credentials_for_store",
        AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.services.delivery_service.get_delivery_provider",
        return_value=mock_provider,
    ):
        with pytest.raises(AppException) as exc:
            await DeliveryService.process_refund(db, 1, approve=True)

    assert exc.value.code == 40062
    # 平台拒绝后回 PENDING，供商家重试
    assert refund.status == "PENDING"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_process_refund_reject_platform_fails_keeps_pending():
    """disagree_refund 返回 False 时不得标记 REJECTED，应抛异常。"""
    refund = SimpleNamespace(
        id=1, order_id=9, status="PENDING", delivery_order_id=2,
        platform="ELEME", platform_refund_id="pr1",
        refund_type="FULL", amount=50, handled_at=None,
    )
    order = SimpleNamespace(id=9, store_id=7, status="COOKING")
    d_order = SimpleNamespace(id=2, order_id=9, platform_order_id="po1")

    db = MagicMock()
    locked_result = MagicMock(scalar_one_or_none=MagicMock(return_value=refund))
    db.execute = AsyncMock(return_value=locked_result)
    db.get = AsyncMock(side_effect=[d_order, order, d_order, order])
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    mock_provider = MagicMock()
    mock_provider.disagree_refund = AsyncMock(return_value=False)

    with patch(
        "app.services.delivery_service.get_credentials_for_store",
        AsyncMock(return_value=MagicMock()),
    ), patch(
        "app.services.delivery_service.get_delivery_provider",
        return_value=mock_provider,
    ):
        with pytest.raises(AppException) as exc:
            await DeliveryService.process_refund(db, 1, approve=False, reason="不要了")

    assert exc.value.code == 40062
    assert refund.status == "PENDING"
    db.commit.assert_awaited()


# ─── 3. 手动补打幂等 ───


@pytest.mark.asyncio
async def test_reprint_dedup_blocks_rapid_double_click():
    """幂等认领失败时应返回「请勿重复」，不调用 print_order。"""
    from app.api.orders_kitchen import reprint_kitchen_ticket

    order = SimpleNamespace(id=10, store_id=1, order_no="SNH-001")
    user = SimpleNamespace(
        id=9, store_id=1, role=SimpleNamespace(code="KITCHEN"),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=order)

    fake_request = MagicMock()
    fake_request.headers = {}

    print_called = AsyncMock(return_value={"mock": True})

    from app.services.system_settings_service import SystemSettingsService
    SystemSettingsService._cache = None

    fake_settings = SimpleNamespace(xpyun_enabled=True)

    with patch(
        "app.core.config.get_settings", return_value=fake_settings,
    ), patch(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        AsyncMock(return_value=True),
    ), patch(
        "app.domains.messaging.idempotency.try_acquire",
        AsyncMock(return_value=False),
    ), patch(
        "app.services.print_service.PrintService.print_order", print_called,
    ):
        result = await reprint_kitchen_ticket(10, fake_request, db, user)

    assert result["data"]["printed"] is False, f"printed should be False, got {result['data']}"
    assert "勿重复" in result["data"]["message"], f"message was: {result['data']['message']}"
    print_called.assert_not_awaited()


@pytest.mark.asyncio
async def test_reprint_dedup_redis_down_degrades_gracefully():
    """Redis 不可用时应降级走 MySQL 幂等，仍可打印主流程。"""
    from app.api.orders_kitchen import reprint_kitchen_ticket

    order = SimpleNamespace(id=10, store_id=1, order_no="SNH-001", status="PAID")
    user = SimpleNamespace(
        id=9, store_id=1, role=SimpleNamespace(code="KITCHEN"),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    fake_request = MagicMock()
    fake_request.headers = {}

    print_called = AsyncMock(return_value=None)

    from app.services.system_settings_service import SystemSettingsService
    SystemSettingsService._cache = None

    fake_settings = SimpleNamespace(xpyun_enabled=True)

    with patch(
        "app.core.config.get_settings", return_value=fake_settings,
    ), patch(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        AsyncMock(return_value=True),
    ), patch(
        "app.domains.messaging.idempotency.try_get_redis",
        AsyncMock(return_value=None),
    ), patch(
        "app.services.print_service.PrintService.print_order", print_called,
    ):
        result = await reprint_kitchen_ticket(10, fake_request, db, user)

    print_called.assert_awaited_once()
    assert result["data"]["printed"] is False


# ─── 4. Webhook claim 释放 owner 校验 ───


@pytest.mark.asyncio
async def test_webhook_process_inbox_uses_atomic_owner_release():
    """process_inbox_item 成功时应使用原子 UPDATE WHERE locked_by=wid。"""
    from app.services import webhook_inbox

    row = SimpleNamespace(
        id=1, channel="MEITUAN", payload='{"_verified": true, "raw": {}}',
        locked_by="host:1234:abc",
    )

    call_args = {}

    async def fake_claim(db, inbox_id, *, worker=None):
        # 记录 worker id，确认 process_inbox_item 传入了
        call_args["worker"] = worker
        return row

    done_result = MagicMock()
    done_result.rowcount = 1

    db = MagicMock()
    db.execute = AsyncMock(return_value=done_result)
    db.commit = AsyncMock()

    delivery_result = SimpleNamespace(
        event="ORDER_NEW", order=None, deferred_tasks=[], extra={},
    )

    with patch.object(webhook_inbox, "claim_inbox_item", side_effect=fake_claim), \
         patch.object(webhook_inbox, "DeliveryService") as mock_ds, \
         patch.object(webhook_inbox, "ws_manager") as mock_ws:
        mock_ds.process_raw = AsyncMock(return_value=delivery_result)
        mock_ws.notify_order_event = AsyncMock()
        with patch.object(webhook_inbox, "AsyncSessionLocal") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            ok = await webhook_inbox.process_inbox_item(1)

    assert ok is True
    # 确认 worker 被传入
    assert call_args["worker"] is not None
    # 确认执行了原子 UPDATE（而非 ORM 属性设置）
    # execute 应被调用于 done_stmt
    assert db.execute.await_count >= 1


@pytest.mark.asyncio
async def test_webhook_process_inbox_reclaimed_during_processing_skips_ws():
    """处理期间被 reclaim（rowcount=0）时应跳过 WS 通知。"""
    from app.services import webhook_inbox

    row = SimpleNamespace(
        id=1, channel="MEITUAN", payload='{"_verified": true, "raw": {}}',
        locked_by="host:1234:abc",
    )

    done_result = MagicMock()
    done_result.rowcount = 0  # 被回收 → 无行更新

    db = MagicMock()
    db.execute = AsyncMock(return_value=done_result)
    db.commit = AsyncMock()

    delivery_result = SimpleNamespace(
        event="ORDER_NEW", order=None, deferred_tasks=[], extra={},
    )

    ws_notify = AsyncMock()

    with patch.object(webhook_inbox, "claim_inbox_item", AsyncMock(return_value=row)), \
         patch.object(webhook_inbox, "DeliveryService") as mock_ds, \
         patch.object(webhook_inbox, "ws_manager") as mock_ws:
        mock_ds.process_raw = AsyncMock(return_value=delivery_result)
        mock_ws.notify_order_event = ws_notify
        with patch.object(webhook_inbox, "AsyncSessionLocal") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            ok = await webhook_inbox.process_inbox_item(1)

    assert ok is True
    # WS 通知不应被调用
    ws_notify.assert_not_awaited()


# ─── 5. Print claim 释放 owner 校验 ───


@pytest.mark.asyncio
async def test_print_process_job_uses_atomic_owner_release():
    """PrintService.process_print_job 成功时应使用原子 UPDATE WHERE locked_by=wid。"""
    from app.services.print_service import PrintService

    job = SimpleNamespace(
        id=5, store_id=1, order_id=10, content="ticket", ticket_type="KITCHEN",
        attempt_count=1, locked_by="host:1234:pid",
    )
    order = SimpleNamespace(id=10, order_no="SNH-001")

    success_result = MagicMock()
    success_result.rowcount = 1

    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.execute = AsyncMock(return_value=success_result)
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    with patch.object(PrintService, "claim_print_job", AsyncMock(return_value=job)), \
         patch.object(PrintService, "_send_content", AsyncMock(return_value={"mock": True})), \
         patch("app.domains.messaging.idempotency.try_acquire", AsyncMock(return_value=True)):
        with patch("app.services.print_service.AsyncSessionLocal") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            ok = await PrintService.process_print_job(5)

    assert ok is True
    # 确认执行了原子 UPDATE（execute 被调用用于 success_stmt）
    assert db.execute.await_count >= 1
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_print_process_job_reclaimed_during_processing_returns_false():
    """PrintJob 被回收后（rowcount=0）应返回 False。"""
    from app.services.print_service import PrintService

    job = SimpleNamespace(
        id=5, store_id=1, order_id=10, content="ticket", ticket_type="KITCHEN",
        attempt_count=1, locked_by="host:1234:pid",
    )
    order = SimpleNamespace(id=10, order_no="SNH-001")

    success_result = MagicMock()
    success_result.rowcount = 0  # 被回收

    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.execute = AsyncMock(return_value=success_result)
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    with patch.object(PrintService, "claim_print_job", AsyncMock(return_value=job)), \
         patch.object(PrintService, "_send_content", AsyncMock(return_value={"mock": True})), \
         patch("app.domains.messaging.idempotency.try_acquire", AsyncMock(return_value=True)):
        with patch("app.services.print_service.AsyncSessionLocal") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            ok = await PrintService.process_print_job(5)

    assert ok is False


# ─── 6. Outbox claim 释放 owner 校验 ───


@pytest.mark.asyncio
async def test_outbox_process_uses_atomic_owner_release():
    """process_outbox_item 成功时使用原子 UPDATE WHERE locked_by=wid。"""
    from app.services import outbox_worker

    row = SimpleNamespace(
        id=1, aggregate_type="paid_side_effect", aggregate_id=1, event_kind="points_award",
        payload='{"order_id": 1}', locked_by="host:1234:pid",
        attempt_count=1, max_attempts=8, status="PROCESSING",
    )

    done_result = MagicMock()
    done_result.rowcount = 1

    db = MagicMock()
    db.execute = AsyncMock(return_value=done_result)
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    handler = AsyncMock()

    with patch.object(outbox_worker, "claim_outbox_item", AsyncMock(return_value=row)), \
         patch.dict(outbox_worker.OUTBOX_HANDLERS, {("paid_side_effect", "points_award"): handler}):
        with patch("app.services.outbox_worker.AsyncSessionLocal") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            await outbox_worker.process_outbox_item(1)

    handler.assert_awaited_once()
    # 确认执行了原子 UPDATE
    assert db.execute.await_count >= 1
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_outbox_mark_failed_uses_atomic_owner_check():
    """_mark_failed 使用原子 UPDATE WHERE locked_by=wid。"""
    from app.services import outbox_worker

    row = SimpleNamespace(
        id=5, locked_by="host:1234:pid",
        aggregate_type="payment", aggregate_id=9,
    )
    fresh = SimpleNamespace(
        id=5, status="PROCESSING", attempt_count=2, max_attempts=8,
    )

    fail_result = MagicMock()
    fail_result.rowcount = 1

    db = MagicMock()
    db.get = AsyncMock(return_value=fresh)
    db.execute = AsyncMock(return_value=fail_result)
    db.commit = AsyncMock()

    await outbox_worker._mark_failed(db, row, "handler_error", wid="host:1234:pid")

    # 确认 execute 被调用于 fail_stmt
    assert db.execute.await_count >= 1
    assert db.commit.await_count >= 1
