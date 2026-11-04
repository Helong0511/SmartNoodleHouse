"""美团 / 饿了么官方签名、事件分类与解析测试"""
import json

import pytest
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
    with patch("app.services.delivery_providers.get_http_client", new=AsyncMock(return_value=client)):
        result = await ElemeProvider()._rpc("eleme.order.confirmOrderLite", {"orderId": "O1"})

    assert result["error"]["code"] == "INVALID_RESPONSE"
