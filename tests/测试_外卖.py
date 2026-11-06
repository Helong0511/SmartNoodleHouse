"""美团 / 饿了么官方签名、事件分类与解析测试"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from decimal import Decimal

from app.services.delivery_events import DeliveryEvent, EVENT_WS_MAP, classify_eleme_event, classify_meituan_event, parse_meituan_foods
from app.services.delivery_providers import ElemeProvider, MeituanProvider
from app.services.delivery_sign import eleme_shop_sign, eleme_v2_sign, meituan_sign


def test_meituan_sign_official_style():
    url = "https://waimaiopen.meituan.com/api/v1/order/confirm"
    params = {"app_id": "1", "order_id": "27061900338318741", "timestamp": "1389751221"}
    secret = "test_secret"
    sig = meituan_sign(url, params, secret)
    assert len(sig) == 32
    assert sig == meituan_sign(url, params, secret)
    params2 = {**params, "sig": "should_ignore"}
    assert meituan_sign(url, params2, secret) == sig


def test_meituan_sign_param_order():
    url = "https://waimaiopen.meituan.com/api/v1/order/preparationMealComplete"
    a = meituan_sign(url, {"timestamp": "1", "app_id": "2", "order_id": "3"}, "sec")
    b = meituan_sign(url, {"order_id": "3", "app_id": "2", "timestamp": "1"}, "sec")
    assert a == b


def test_eleme_v2_sign_official_example():
    path = "http://ppe-openapi.ele.me/v2/restaurants/"
    params = {"consumer_key": "7284397484", "timestamp": "1374908054"}
    secret = "4d31ba58fd73c71db697ab5e4946d52d"
    sig = eleme_v2_sign(path, params, secret)
    assert sig == "e76dfee7276f0c7a382b4f0dbdad802a95c642aa"


def test_eleme_shop_sign_stable():
    action = "eleme.order.confirmOrderLite"
    token = "tok"
    metas = {"app_key": "key", "timestamp": 123}
    params = {"orderId": "999"}
    s1 = eleme_shop_sign(action, token, metas, params, "secret")
    s2 = eleme_shop_sign(action, token, metas, params, "secret")
    assert s1 == s2
    assert len(s1) == 32
    assert s1 == s1.upper()


def test_meituan_parse_order():
    raw = {
        "_dev_simulate": True,
        "order_id": "MT10001",
        "day_seq": "88",
        "store_id": 1,
        "total": "25.00",
        "recipient_name": "王先生",
        "recipient_phone": "13800000000",
        "recipient_address": "测试路 1 号",
        "items": [
            {"sku_id": "SKU_BEEF", "name": "牛肉面", "quantity": 1, "price": "18.00"},
        ],
    }
    parsed = MeituanProvider().parse_order(raw)
    assert parsed["platform_order_id"] == "MT10001"
    assert parsed["total_amount"] == Decimal("25.00")


def test_eleme_parse_order():
    raw = {
        "_dev_simulate": True,
        "type": "217",
        "message": {
            "order": {
                "id": "EL20002",
                "daySn": "12",
                "totalPrice": "30.00",
                "consignee": {"name": "李女士", "phone": "13900000000"},
                "address": "幸福小区 3 栋",
                "groups": [{"items": [{"vfoodId": "E_BEEF", "name": "牛肉面", "quantity": 1, "price": "18.00"}]}],
            }
        },
    }
    parsed = ElemeProvider().parse_order(raw)
    assert parsed["platform_order_id"] == "EL20002"
    assert parsed["recipient_name"] == "李女士"


def test_classify_meituan_events():
    assert classify_meituan_event({"status": 9, "order_id": "1"}) == DeliveryEvent.ORDER_CANCEL
    assert classify_meituan_event({"status": 8, "order_id": "1"}) == DeliveryEvent.ORDER_COMPLETED
    assert classify_meituan_event({"notify_type": "urge", "order_id": "1"}) == DeliveryEvent.URGE
    assert classify_meituan_event({"refund_id": "R1", "order_id": "1"}) == DeliveryEvent.REFUND
    assert classify_meituan_event({"logistics_status": "DELIVERING", "order_id": "1"}) == DeliveryEvent.DELIVERY
    assert classify_meituan_event({"_dev_simulate": True, "order_id": "1", "items": []}) == DeliveryEvent.ORDER_NEW


def test_event_ws_map():
    assert EVENT_WS_MAP[DeliveryEvent.URGE] == "ORDER_URGE"
    assert EVENT_WS_MAP[DeliveryEvent.REFUND] == "ORDER_REFUND"
    assert EVENT_WS_MAP[DeliveryEvent.ORDER_CANCEL] == "ORDER_CANCELLED"


def test_classify_eleme_events():
    assert classify_eleme_event({"type": "233"}) == DeliveryEvent.PRIVACY
    assert classify_eleme_event({"type": "217"}) == DeliveryEvent.ORDER_NEW
    assert classify_eleme_event({"type": "14"}) == DeliveryEvent.ORDER_CANCEL
    assert classify_eleme_event({"type": "218"}) == DeliveryEvent.URGE
    assert classify_eleme_event({"type": "20"}) == DeliveryEvent.REFUND
    assert classify_eleme_event({"type": "231"}) == DeliveryEvent.DELIVERY
    assert classify_eleme_event({"type": "219"}) == DeliveryEvent.ORDER_COMPLETED


def test_parse_meituan_foods_detail_json():
    detail = json.dumps([{"app_food_code": "P001", "food_name": "牛肉面", "quantity": 2, "price": "18", "spec": "大份"}])
    items = parse_meituan_foods({"detail": detail})
    assert len(items) == 1
    assert items[0]["app_food_code"] == "P001"
    assert items[0]["quantity"] == 2
    assert items[0]["options"][0]["value"] == "大份"


@pytest.mark.asyncio
async def test_meituan_rejects_http_500_even_if_json_claims_success(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.delivery_providers import MeituanProvider

    url = "https://waimaiopen.meituan.com/api/v1/order/confirm"
    response = httpx.Response(
        500,
        json={"data": "ok"},
        request=httpx.Request("POST", url),
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response), get=AsyncMock())
    settings = SimpleNamespace(
        meituan_app_id="app",
        meituan_app_secret="secret",
        meituan_api_base="https://waimaiopen.meituan.com",
        is_production=True,
        allow_delivery_mock=False,
    )
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        with pytest.raises(httpx.HTTPStatusError):
            await MeituanProvider()._request("/api/v1/order/confirm", {"order_id": "O1"}, method="POST")


@pytest.mark.asyncio
async def test_eleme_rejects_http_500_empty_json_response(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.delivery_providers import ElemeProvider

    url = "https://open-api.shop.ele.me/"
    response = httpx.Response(
        500,
        json={},
        request=httpx.Request("POST", url),
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    settings = SimpleNamespace(
        eleme_app_id="app",
        eleme_app_secret="secret",
        eleme_access_token="token",
        eleme_api_base="https://open-api.shop.ele.me",
        is_production=True,
        allow_delivery_mock=False,
    )
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        with pytest.raises(httpx.HTTPStatusError):
            await ElemeProvider()._rpc("eleme.order.confirmOrderLite", {"orderId": "O1"})


@pytest.mark.asyncio
async def test_eleme_rejects_http_200_empty_json_envelope(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.delivery_providers import ElemeProvider

    url = "https://open-api.shop.ele.me/"
    response = httpx.Response(
        200,
        json={},
        request=httpx.Request("POST", url),
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    settings = SimpleNamespace(
        eleme_app_id="app",
        eleme_app_secret="secret",
        eleme_access_token="token",
        eleme_api_base="https://open-api.shop.ele.me",
        is_production=True,
        allow_delivery_mock=False,
    )
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    from app.services.delivery_providers import DeliveryPlatformResultUnknown

    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        with pytest.raises(DeliveryPlatformResultUnknown):
            await ElemeProvider()._rpc("eleme.order.confirmOrderLite", {"orderId": "O1"})


@pytest.mark.asyncio
async def test_eleme_sync_product_requires_item_id_and_applies_off_shelf_state(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.delivery_providers import ElemeProvider

    settings = SimpleNamespace(eleme_app_id="app", is_production=False, allow_delivery_mock=False)
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    provider = ElemeProvider()
    provider._rpc = AsyncMock(return_value={"result": {"itemId": "ELEME-ITEM-1"}})
    shelf_calls = []

    async def _set_shelf(items, on_shelf):
        shelf_calls.append((items, on_shelf))
        return len(items)

    provider.set_sell_status = _set_shelf
    result = await provider.sync_product(
        {
            "shop_id": "SHOP-1", "name": "牛肉面", "app_food_code": "NOODLE-1",
            "price": "18.00", "stock": 0, "is_sold_out": 1,
        }
    )
    assert result["ok"] is True
    assert result["platform_id"] == "ELEME-ITEM-1"
    assert shelf_calls and shelf_calls[0][1] is False


@pytest.mark.asyncio
async def test_eleme_sync_product_does_not_create_mapping_without_item_id(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.delivery_providers import ElemeProvider

    settings = SimpleNamespace(eleme_app_id="app", is_production=False, allow_delivery_mock=False)
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    provider = ElemeProvider()
    provider._rpc = AsyncMock(return_value={"result": {}})
    provider.set_sell_status = AsyncMock(return_value=1)
    result = await provider.sync_product(
        {"shop_id": "SHOP-1", "name": "牛肉面", "app_food_code": "NOODLE-1", "price": "18.00"}
    )
    assert result["ok"] is False
    assert result["platform_id"] is None
    provider.set_sell_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_callback_log_fallback_event_id_is_content_stable_and_distinct():
    """无平台事件 ID 时，同订单同类型的不同回调不能因唯一键冲突而丢失。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    db = MagicMock()
    db.flush = AsyncMock()
    first = {"status": "PREPARING", "sequence": 1}
    changed = {"status": "PREPARING", "sequence": 2}

    await DeliveryService._log_callback(db, "MEITUAN", "ORDER_STATUS", "ORDER-1", 10, first)
    first_row = db.add.call_args.args[0]
    await DeliveryService._log_callback(db, "MEITUAN", "ORDER_STATUS", "ORDER-1", 10, first)
    duplicate_row = db.add.call_args.args[0]
    await DeliveryService._log_callback(db, "MEITUAN", "ORDER_STATUS", "ORDER-1", 10, changed)
    changed_row = db.add.call_args.args[0]

    assert first_row.event_id == duplicate_row.event_id
    assert first_row.event_id != changed_row.event_id
    assert len(first_row.event_id) <= 128


def test_refund_event_fingerprint_is_stable_without_provider_refund_id():
    from decimal import Decimal

    from app.services.delivery_service import _refund_event_fingerprint

    raw_a = {"refund_amount": "12.00", "reason": "顾客申请", "status": "SUCCESS"}
    raw_b = {"status": "SUCCESS", "reason": "顾客申请", "refund_amount": "12.00"}

    first = _refund_event_fingerprint("MEITUAN", 88, Decimal("12.00"), "PARTIAL", raw_a)
    retry = _refund_event_fingerprint("meituan", 88, Decimal("12.00"), "PARTIAL", raw_b)
    changed = _refund_event_fingerprint(
        "MEITUAN", 88, Decimal("15.00"), "PARTIAL", {"refund_amount": "15.00", "status": "SUCCESS"}
    )

    assert first == retry
    assert first != changed
    assert len(first) == 64


def test_meituan_refund_no_is_classified_as_refund_event():
    from app.services.delivery_events import DeliveryEvent, classify_meituan_event

    assert classify_meituan_event({"refund_no": "RF-2026-01", "order_id": "OD-1"}) == DeliveryEvent.REFUND



@pytest.mark.asyncio
@pytest.mark.parametrize("refund_id", ["RF-DUP-1", None])
async def test_duplicate_delivery_refund_callback_does_not_regress_terminal_summary(
    monkeypatch, refund_id
):
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from sqlalchemy.exc import IntegrityError

    from app.services.delivery_service import DeliveryService

    delivery_order = SimpleNamespace(
        id=88, order_id=99, platform="MEITUAN", refund_status="APPROVED",
        last_sync_at=None,
    )
    order = SimpleNamespace(id=99, total_amount=Decimal("30.00"))
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()

    class _DuplicateSavepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            if exc_type is None:
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            return False

    db.begin_nested.return_value = _DuplicateSavepoint()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=delivery_order)
    )

    raw = {"refund_amount": "10.00", "status": "SUCCESS"}
    parsed = {"platform_order_id": "PLATFORM-ORDER-1", "refund_amount": "10.00"}
    if refund_id:
        raw["refund_id"] = refund_id
        parsed["refund_id"] = refund_id

    result = await DeliveryService._handle_refund(
        db, "MEITUAN", parsed, raw
    )

    assert result is order
    assert delivery_order.refund_status == "APPROVED"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_new_delivery_refund_callback_sets_pending_summary(monkeypatch):
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    delivery_order = SimpleNamespace(
        id=89, order_id=100, platform="MEITUAN", refund_status="NONE",
        last_sync_at=None,
    )
    order = SimpleNamespace(id=100, total_amount=Decimal("30.00"))
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()

    class _Savepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    db.begin_nested.return_value = _Savepoint()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=delivery_order)
    )

    result = await DeliveryService._handle_refund(
        db,
        "MEITUAN",
        {"platform_order_id": "PLATFORM-ORDER-2", "refund_amount": "10.00", "refund_id": "RF-NEW"},
        {"refund_amount": "10.00", "refund_id": "RF-NEW"},
    )

    assert result is order
    assert delivery_order.refund_status == "PENDING"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_delivery_completed_callback_syncs_summary_when_order_already_completed(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=91, order_id=101, delivery_status="ACCEPTED",
        logistics_status="DELIVERING", completed_at=None, last_sync_at=None,
    )
    order = SimpleNamespace(id=101, status="COMPLETED", completed_at=None)
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    )

    result = await DeliveryService._handle_completed(db, "MEITUAN", "PLATFORM-101")

    assert result is order
    assert d_order.delivery_status == "DONE"
    assert d_order.logistics_status == "DELIVERED"
    assert d_order.completed_at is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_cancel_callback_syncs_summary_when_order_already_cancelled(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=92, order_id=102, delivery_status="ACCEPTED", last_sync_at=None,
    )
    order = SimpleNamespace(id=102, status="CANCELLED")
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    )

    result = await DeliveryService._handle_cancel(db, "MEITUAN", "PLATFORM-102")

    assert result is order
    assert d_order.delivery_status == "CANCELLED"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["CANCELLED", "REFUNDED"])
async def test_late_delivery_completed_callback_does_not_override_other_terminal_status(
    monkeypatch, terminal_status
):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=93, order_id=103, delivery_status="CANCELLED",
        logistics_status="CANCELLED", completed_at=None, last_sync_at=None,
    )
    order = SimpleNamespace(id=103, status=terminal_status, completed_at=None)
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    )

    result = await DeliveryService._handle_completed(db, "MEITUAN", "PLATFORM-103")

    assert result is order
    assert d_order.delivery_status == "CANCELLED"
    assert d_order.logistics_status == "CANCELLED"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["COMPLETED", "REFUNDED"])
async def test_late_delivery_cancel_callback_does_not_override_other_terminal_status(
    monkeypatch, terminal_status
):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=94, order_id=104, delivery_status="DONE",
        last_sync_at=None,
    )
    order = SimpleNamespace(id=104, status=terminal_status)
    db = MagicMock()
    db.get = AsyncMock(return_value=order)
    db.flush = AsyncMock()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    )

    result = await DeliveryService._handle_cancel(db, "MEITUAN", "PLATFORM-104")

    assert result is order
    assert d_order.delivery_status == "DONE"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_status,delivery_status",
    [
        ("COMPLETED", "ACCEPTED"),
        ("CANCELLED", "ACCEPTED"),
        ("REFUNDED", "ACCEPTED"),
        ("PREPARING", "DONE"),
        ("PREPARING", "CANCELLED"),
    ],
)
async def test_late_delivery_progress_callback_does_not_regress_terminal_state(
    monkeypatch, order_status, delivery_status
):
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=95,
        order_id=105,
        delivery_status=delivery_status,
        logistics_status="DELIVERED" if delivery_status == "DONE" else "CANCELLED",
        logistics_code="FINAL",
        rider_name="已确认骑手",
        rider_phone="13800000000",
        last_sync_at=None,
    )
    order = SimpleNamespace(id=105, status=order_status)
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: order)
    )
    db.flush = AsyncMock()
    monkeypatch.setattr(
        DeliveryService, "_get_delivery_order", AsyncMock(return_value=d_order)
    )

    result = await DeliveryService._handle_delivery(
        db,
        "MEITUAN",
        {
            "platform_order_id": "PLATFORM-105",
            "logistics_status": "DELIVERING",
            "logistics_code": "STALE",
            "rider_name": "迟到的旧骑手",
            "rider_phone": "13900000000",
        },
        {},
    )

    assert result is order
    assert d_order.logistics_status in ("DELIVERED", "CANCELLED")
    assert d_order.logistics_code == "FINAL"
    assert d_order.rider_name == "已确认骑手"
    assert d_order.rider_phone == "13800000000"
    assert d_order.last_sync_at is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_callback_log_fallback_id_is_canonical_and_duplicate_safe():
    from unittest.mock import AsyncMock, MagicMock

    from app.services.delivery_service import DeliveryService

    raw_a = {"order_id": "order-2", "status": "DELIVERING", "code": "10"}
    raw_b = {"code": "10", "status": "DELIVERING", "order_id": "order-2"}
    db = MagicMock()
    db.flush = AsyncMock(
        side_effect=[None, IntegrityError("insert", {}, Exception("duplicate event"))]
    )
    db.begin_nested.side_effect = lambda: _CallbackLogSavepoint(db)

    async def execute_existing(_stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: object())

    db.execute = AsyncMock(side_effect=execute_existing)

    await DeliveryService._log_callback(
        db, "MEITUAN", "DELIVERY", "order-2", None, raw_a
    )
    first_id = db.add.call_args_list[-1].args[0].event_id
    await DeliveryService._log_callback(
        db, "MEITUAN", "DELIVERY", "order-2", None, raw_b
    )
    second_id = db.add.call_args_list[-1].args[0].event_id

    assert first_id == second_id
    assert db.flush.await_count == 2


class _CallbackLogSavepoint:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is IntegrityError:
            return False
        return False


@pytest.mark.asyncio
async def test_merchant_cancel_persists_one_outbox_event_and_does_not_call_platform(monkeypatch):
    """商家取消只持久化意图；并发重复请求复用唯一 Outbox 事件。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.services import delivery_service
    from app.services.delivery_service import DeliveryService

    d_order = SimpleNamespace(
        id=501, order_id=601, platform="MEITUAN",
        platform_order_id="MT-ORDER-501", delivery_status="ACCEPTED",
    )
    order = SimpleNamespace(id=601, store_id=7, status="CONFIRMED")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: 601),
        SimpleNamespace(scalar_one_or_none=lambda: order),
        SimpleNamespace(scalar_one_or_none=lambda: d_order),
    ])
    db.commit = AsyncMock()

    monkeypatch.setattr(
        delivery_service, "get_credentials_for_store",
        AsyncMock(return_value=SimpleNamespace(platform_shop_id="shop-7")),
    )
    event = SimpleNamespace(id=9001, status="PENDING")
    enqueue = AsyncMock(return_value=(event, True))
    monkeypatch.setattr(delivery_service, "enqueue_outbox", enqueue)

    result = await DeliveryService.cancel_by_merchant(db, 501, "商家缺货")

    assert result == {
        "ok": False, "status": "PENDING", "order_id": 601,
        "outbox_id": 9001, "queued": True,
    }
    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["aggregate_type"] == "webhook_task"
    assert kwargs["aggregate_id"] == 501
    assert kwargs["event_kind"] == "deferred_cancel_order"
    assert kwargs["payload"]["action"] == "cancel_order"
    assert kwargs["payload"]["store_id"] == 7
    assert kwargs["payload"]["platform_order_id"] == "MT-ORDER-501"
    assert "credentials" not in kwargs["payload"]
    db.commit.assert_awaited_once()
    assert db.execute.await_count == 3
    # 先读关联 ID，再锁 Order，最后锁 DeliveryOrder，避免与配送回调的锁顺序相反。
    order_lock_sql = str(db.execute.await_args_list[1].args[0]).lower()
    delivery_lock_sql = str(db.execute.await_args_list[2].args[0]).lower()
    assert "orders" in order_lock_sql
    assert "delivery_orders" in delivery_lock_sql


@pytest.mark.asyncio
async def test_merchant_cancel_outbox_handler_updates_local_state_only_after_platform_success(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.services import delivery_tasks
    from app.services.delivery_tasks import execute_webhook_outbox_task

    order = SimpleNamespace(id=601, status="CONFIRMED")
    d_order = SimpleNamespace(id=501, order_id=601, delivery_status="ACCEPTED", last_sync_at=None)
    db = MagicMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, key: order if model.__name__ == "Order" else d_order)
    db.flush = AsyncMock()

    provider = SimpleNamespace(cancel_order=AsyncMock(return_value=True))
    monkeypatch.setattr(
        delivery_tasks, "get_credentials_for_store",
        AsyncMock(return_value=SimpleNamespace(platform_shop_id="shop-7")),
    )
    monkeypatch.setattr(delivery_tasks, "get_delivery_provider", lambda *_args: provider)

    async def apply_status(_db, target, status, **_kwargs):
        target.status = status

    monkeypatch.setattr("app.services.order_lifecycle.apply_order_status", apply_status)

    row = SimpleNamespace(
        aggregate_id=501,
        payload=json.dumps({
            "action": "cancel_order", "platform": "MEITUAN",
            "platform_order_id": "MT-ORDER-501", "store_id": 7,
            "order_id": 601, "reason": "商家缺货",
        }),
    )
    await execute_webhook_outbox_task(db, row)

    provider.cancel_order.assert_awaited_once_with("MT-ORDER-501", "商家缺货")
    assert order.status == "CANCELLED"
    assert d_order.delivery_status == "CANCELLED"
    assert d_order.last_sync_at is not None
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_meituan_http_200_non_json_is_uncertain(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.delivery_providers import DeliveryPlatformResultUnknown, MeituanProvider

    url = "https://waimaiopen.meituan.com/api/v1/order/cancel"
    response = httpx.Response(
        200,
        text="gateway returned an unreadable response",
        request=httpx.Request("GET", url),
    )
    client = SimpleNamespace(get=AsyncMock(return_value=response), post=AsyncMock())
    settings = SimpleNamespace(
        meituan_app_id="app",
        meituan_app_secret="secret",
        meituan_api_base="https://waimaiopen.meituan.com",
        is_production=True,
        allow_delivery_mock=False,
    )
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        with pytest.raises(DeliveryPlatformResultUnknown):
            await MeituanProvider()._request("/api/v1/order/cancel", {"order_id": "O1"})


@pytest.mark.asyncio
async def test_eleme_http_200_non_json_is_uncertain(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.delivery_providers import DeliveryPlatformResultUnknown, ElemeProvider

    url = "https://open-api.shop.ele.me/"
    response = httpx.Response(
        200,
        text="invalid rpc response",
        request=httpx.Request("POST", url),
    )
    client = SimpleNamespace(post=AsyncMock(return_value=response))
    settings = SimpleNamespace(
        eleme_app_id="app",
        eleme_app_secret="secret",
        eleme_access_token="token",
        eleme_api_base="https://open-api.shop.ele.me",
        is_production=True,
        allow_delivery_mock=False,
    )
    monkeypatch.setattr("app.services.delivery_providers.get_settings", lambda: settings)
    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        with pytest.raises(DeliveryPlatformResultUnknown):
            await ElemeProvider()._rpc("eleme.order.cancelOrderLite", {"orderId": "O1"})
