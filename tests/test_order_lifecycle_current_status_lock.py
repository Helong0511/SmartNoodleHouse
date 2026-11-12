"""并发订单状态迁移后，调用方 ORM 对象必须同步到锁读到的权威状态。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Order
from app.services.order_lifecycle import apply_order_status


@pytest.mark.asyncio
async def test_terminal_replay_refreshes_stale_order_status_without_stock_restore(monkeypatch):
    # 模拟另一个事务已先把数据库订单置为 REFUNDED；当前调用仍持有 PAID 快照。
    order = Order(
        id=41,
        status="PAID",
        payment_status="REFUNDED",
        store_id=7,
    )
    restore_stock = AsyncMock()
    monkeypatch.setattr("app.services.order_lifecycle._restore_order_stock", restore_stock)
    result = MagicMock()
    result.scalar_one_or_none.return_value = "REFUNDED"
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()

    returned = await apply_order_status(
        db,
        order,
        "REFUNDED",
        source="PAYMENT",
    )

    assert returned is order
    assert order.status == "REFUNDED"
    assert db.execute.await_count == 1
    locked_query = db.execute.await_args.args[0]
    assert locked_query._for_update_arg is not None
    db.flush.assert_not_awaited()
    restore_stock.assert_not_awaited()
