"""小程序扫码登录与桌台会话回归测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.api import miniprogram as miniprogram_api


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/miniprogram/alipay/login",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
        }
    )
    request.state.request_id = "test-request"
    return request


@pytest.mark.asyncio
async def test_alipay_login_passes_table_record_to_dining_session(monkeypatch):
    """登录响应可以返回桌台字典，但会话服务必须收到 ORM 桌台记录。"""
    customer = SimpleNamespace(
        id=7,
        customer_no="CU-TEST",
        status=1,
        first_seen_store_id=None,
    )
    table_record = SimpleNamespace(
        id=11,
        store_id=3,
        table_no="A01",
        table_name="1号桌",
        qr_token="table-token",
    )
    table_payload = {"id": 11, "store_id": 3, "table_no": "A01"}
    session = SimpleNamespace(id=12)

    class Result:
        def scalar_one_or_none(self):
            return None

    db = SimpleNamespace(
        execute=AsyncMock(return_value=Result()),
        flush=AsyncMock(),
    )
    remember = AsyncMock()
    start_session = AsyncMock(return_value=session)

    async def fake_auth_code2user(self, _auth_code):
        return {"alipay_user_id": "ali-test", "is_mock": True}

    async def fake_get_or_create(_db, _user_id):
        return customer

    async def fake_payload(*_args, **kwargs):
        assert kwargs["table"] is table_payload
        assert kwargs["dining_session"] is session
        return {"table": table_payload}

    monkeypatch.setattr(miniprogram_api.AlipayMiniAuth, "auth_code2user", fake_auth_code2user)
    monkeypatch.setattr(miniprogram_api.CustomerService, "get_or_create_by_alipay", fake_get_or_create)
    monkeypatch.setattr(miniprogram_api.CustomerService, "remember_first_seen", remember)
    monkeypatch.setattr(miniprogram_api, "_resolve_table_record", AsyncMock(return_value=table_record))
    monkeypatch.setattr(miniprogram_api, "_table_payload", AsyncMock(return_value=table_payload))
    monkeypatch.setattr(miniprogram_api.DiningSessionService, "start_or_refresh", start_session)
    monkeypatch.setattr(miniprogram_api, "_channel_login_payload", fake_payload)

    response = await miniprogram_api.alipay_mini_login(
        miniprogram_api.AlipayMiniLoginRequest(
            auth_code="mock:test",
            table_token="table-token",
        ),
        _request(),
        db,
    )

    remember.assert_awaited_once_with(db, customer, "table-token", table=table_record)
    start_session.assert_awaited_once_with(
        db,
        customer_id=customer.id,
        table=table_record,
        table_token="table-token",
    )
    assert response["data"]["table"] is table_payload
