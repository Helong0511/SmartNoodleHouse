"""并发守卫回归：订单状态机当前读加锁、WS 计数 Lua 原子释放、催单计数原子自增。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql import Update

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_apply_order_status_revalidates_under_row_lock():
    """内存快照是 PAID，但行锁当前读发现已被并发退款置为 REFUNDED。

    目标 CANCELLED 必须被拒绝（状态机 REFUNDED 为终态），不得覆写、不得回补库存。
    """
    from app.services.order_lifecycle import apply_order_status

    order = MagicMock()
    order.id = 1
    order.status = "PAID"  # 旧快照

    locked_result = MagicMock()
    locked_result.scalar_one_or_none = MagicMock(return_value="REFUNDED")
    db = MagicMock()
    db.execute = AsyncMock(return_value=locked_result)
    db.flush = AsyncMock()

    with pytest.raises(AppException) as ei:
        await apply_order_status(
            db, order, "CANCELLED", source="DELIVERY", assert_kind="SYSTEM"
        )
    assert ei.value.code == 40025
    # 状态读取必须是 FOR UPDATE 当前读
    stmt = db.execute.call_args.args[0]
    assert stmt._for_update_arg is not None
    # 未写入、未回补库存
    assert order.status == "PAID"
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_apply_order_status_idempotent_when_db_already_target():
    """当前读已是目标状态：幂等返回，不重复写历史/回库存。"""
    from app.services.order_lifecycle import apply_order_status

    order = MagicMock()
    order.id = 2
    order.status = "PAID"  # 快照落后
    order.payment_status = "PAID"

    locked_result = MagicMock()
    locked_result.scalar_one_or_none = MagicMock(return_value="CANCELLED")
    db = MagicMock()
    db.execute = AsyncMock(return_value=locked_result)
    db.flush = AsyncMock()

    out = await apply_order_status(
        db, order, "CANCELLED", source="DELIVERY", assert_kind="SYSTEM"
    )
    assert out is order
    db.flush.assert_not_called()
    assert order.status == "PAID"  # 幂等返回不重新赋值


@pytest.mark.asyncio
async def test_ws_quota_release_uses_atomic_lua():
    """release 必须用 Lua 原子 DECR+条件 DEL，不再分两条命令（避免误删新连接计数）。"""
    from app.services import ws_conn_quota

    r = MagicMock()
    r.eval = AsyncMock()
    with patch("app.core.redis_client.get_redis", AsyncMock(return_value=r)):
        await ws_conn_quota.release(ip="1.2.3.4", user_key="u7")

    assert r.eval.await_count == 2  # ip + user
    r.decr.assert_not_called()
    r.delete.assert_not_called()


@pytest.mark.asyncio
async def test_handle_urge_uses_atomic_increment():
    """催单计数必须走 SQL 原子自增，而不是读旧值后 Python +1。"""
    from app.services.delivery_service import DeliveryService

    d_order = MagicMock()
    d_order.id = 55
    d_order.order_id = 99
    d_order.platform_order_id = "MT123"

    sel_result = MagicMock()
    sel_result.scalar_one_or_none = MagicMock(return_value=d_order)
    upd_result = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[sel_result, upd_result])
    db.get = AsyncMock(return_value=None)

    order, tasks = await DeliveryService._handle_urge(
        db, "meituan", "MT123", {"x": 1}, None
    )
    assert order is None
    assert len(tasks) == 1 and tasks[0]["action"] == "reply_urge"
    stmt = db.execute.call_args_list[1].args[0]
    assert isinstance(stmt, Update)
