"""顾客订单 WebSocket 必须按顾客 ID 定向，不能广播同店其他顾客订单。"""
import pytest

from app.services.websocket_manager import ConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_customer_order_event_only_reaches_owner_and_store_staff():
    manager = ConnectionManager()
    customer_a = FakeWebSocket()
    customer_b = FakeWebSocket()
    store_staff = FakeWebSocket()
    manager.active["orders:customer:101"] = {customer_a}
    manager.active["orders:customer:202"] = {customer_b}
    manager.active["orders:7"] = {store_staff}

    await manager.local_broadcast(
        "orders",
        "ORDER_PAID",
        {"order_id": 55},
        store_id=7,
        customer_id=101,
    )

    assert len(customer_a.messages) == 1
    assert customer_b.messages == []
    assert len(store_staff.messages) == 1
