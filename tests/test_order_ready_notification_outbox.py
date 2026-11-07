"""Regression tests for durable order-ready subscription notifications."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_order_ready_notification_is_enqueued_for_originating_channel_in_calling_transaction(monkeypatch):
    from app.services import order_lifecycle, outbox_worker

    enqueue = AsyncMock(return_value=(MagicMock(), True))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)
    db = object()
    order = SimpleNamespace(id=321, customer_id=77, channel="WECHAT")

    await order_lifecycle.notify_order_ready_subscribe(db, order)

    assert enqueue.await_count == 1
    assert enqueue.await_args.kwargs["event_kind"] == "order_ready_wechat"
    assert enqueue.await_args.kwargs["aggregate_type"] == "order_notification"
    assert enqueue.await_args.args[0] is db
    assert enqueue.await_args.kwargs["payload"]["channel"] == "wechat"


@pytest.mark.asyncio
async def test_order_ready_handler_skips_notification_if_order_is_no_longer_ready():
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="COMPLETED", table_id=None, order_no="A321"
    ))

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_wechat")
    )

    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_order_ready_wechat_handler_sends_only_wechat(monkeypatch):
    from app.services import wechat_mini_client
    from app.services.customer_service import CustomerService
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="READY", table_id=None, order_no="A321"
    ))
    monkeypatch.setattr(
        CustomerService, "resolve_wechat_openid", AsyncMock(return_value="wx-openid")
    )
    wx_send = AsyncMock(return_value=True)
    monkeypatch.setattr(wechat_mini_client, "send_order_ready_subscribe", wx_send)

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_wechat")
    )

    wx_send.assert_awaited_once_with(
        openid="wx-openid", order_no="A321", order_id=321, table_label=None
    )


@pytest.mark.asyncio
async def test_order_ready_alipay_handler_sends_only_alipay(monkeypatch):
    from app.services import alipay_mini_client
    from app.services.customer_service import CustomerService
    from app.services.paid_side_effects import _handle_order_ready_notification

    db = MagicMock()
    db.get = AsyncMock(return_value=SimpleNamespace(
        id=321, customer_id=77, status="READY", table_id=None, order_no="A321"
    ))
    monkeypatch.setattr(
        CustomerService, "resolve_alipay_user_id", AsyncMock(return_value="ali-user")
    )
    ali_send = AsyncMock(return_value=True)
    monkeypatch.setattr(alipay_mini_client, "send_order_ready_subscribe", ali_send)

    await _handle_order_ready_notification(
        db, SimpleNamespace(aggregate_id=321, event_kind="order_ready_alipay")
    )

    ali_send.assert_awaited_once_with(
        alipay_user_id="ali-user", order_no="A321", order_id=321, table_label=None
    )


@pytest.mark.parametrize("event_kind", ["order_ready_wechat", "order_ready_alipay"])
def test_order_ready_outbox_is_treated_as_external_side_effect(event_kind):
    from app.services.outbox_worker import _is_external_side_effect_outbox

    assert _is_external_side_effect_outbox(SimpleNamespace(
        aggregate_type="order_notification", event_kind=event_kind, payload="{}"
    ))


@pytest.mark.asyncio
async def test_stale_order_ready_notification_is_reclaimed_as_uncertain_not_replayed():
    from app.services.outbox_worker import reclaim_stale_processing

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    await reclaim_stale_processing(db, stale_after=300)

    statement = db.execute.await_args.args[0]
    compiled = statement.compile()
    values = [str(value) for value in compiled.params.values()]
    assert "order_notification" in values
    assert "UNCERTAIN" in values
    literal_sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "EXTERNAL_RESULT_UNKNOWN" in literal_sql


@pytest.mark.asyncio
async def test_order_ready_outbox_enqueue_failure_is_not_silenced(monkeypatch):
    from app.services import order_lifecycle, outbox_worker

    enqueue = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(outbox_worker, "enqueue_outbox", enqueue)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await order_lifecycle.notify_order_ready_subscribe(
            object(), SimpleNamespace(id=321, customer_id=77, channel="WECHAT")
        )


@pytest.mark.parametrize(
    ("module_name", "provider_name"),
    [
        ("app.services.wechat_mini_client", "WECHAT"),
        ("app.services.alipay_mini_client", "ALIPAY"),
    ],
)
def test_notification_transport_errors_distinguish_safe_connect_retry_from_unknown_result(
    module_name, provider_name
):
    import importlib
    import httpx

    from app.services.outbox_worker import ExternalSideEffectUncertain

    module = importlib.import_module(module_name)
    classify = module._raise_order_ready_transport_error

    # Connection setup failed before the request could be delivered: normal retry is safe.
    connect_error = httpx.ConnectError("connection refused")
    with pytest.raises(httpx.ConnectError):
        classify(connect_error)

    # A response timeout after sending may mean the provider already accepted the message.
    # It must enter UNCERTAIN rather than automatically sending a duplicate.
    with pytest.raises(ExternalSideEffectUncertain, match=provider_name):
        classify(httpx.ReadTimeout("response timed out"))


@pytest.mark.asyncio
@pytest.mark.parametrize("token_error", [40001, 40014, 42001])
async def test_wechat_order_ready_refreshes_invalid_access_token_once(monkeypatch, token_error):
    from types import SimpleNamespace

    from app.services import wechat_mini_client
    from app.services.system_settings_service import SystemSettingsService

    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(
            wechat_app_id="wx-app",
            wechat_app_secret="wx-secret",
            wechat_subscribe_template_order_ready="WX_READY",
        ),
    )
    monkeypatch.setattr(
        SystemSettingsService,
        "resolved_str_sync",
        staticmethod(lambda key, default="": "WX_READY"),
    )
    token = AsyncMock(side_effect=["expired-token", "fresh-token"])
    monkeypatch.setattr(wechat_mini_client, "get_access_token", token)

    responses = [{"errcode": token_error, "errmsg": "invalid access token"}, {"errcode": 0}]
    requests = []

    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            requests.append((url, json))
            return Response(responses.pop(0))

    monkeypatch.setattr(wechat_mini_client.httpx, "AsyncClient", Client)

    assert await wechat_mini_client.send_order_ready_subscribe(
        openid="wx-openid", order_no="SNH-1", order_id=1
    )
    assert token.await_count == 2
    assert token.await_args_list[0].kwargs == {"force": False}
    assert token.await_args_list[1].kwargs == {"force": True}
    assert len(requests) == 2
    assert "access_token=expired-token" in requests[0][0]
    assert "access_token=fresh-token" in requests[1][0]


@pytest.mark.asyncio
async def test_wechat_order_ready_does_not_retry_user_rejected_subscription(monkeypatch):
    from types import SimpleNamespace

    from app.services import wechat_mini_client
    from app.services.system_settings_service import SystemSettingsService

    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(
            wechat_app_id="wx-app",
            wechat_app_secret="wx-secret",
            wechat_subscribe_template_order_ready="WX_READY",
        ),
    )
    monkeypatch.setattr(
        SystemSettingsService,
        "resolved_str_sync",
        staticmethod(lambda key, default="": "WX_READY"),
    )
    token = AsyncMock(return_value="valid-token")
    monkeypatch.setattr(wechat_mini_client, "get_access_token", token)

    class Response:
        def json(self):
            return {"errcode": 43101, "errmsg": "user refuse"}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            return Response()

    monkeypatch.setattr(wechat_mini_client.httpx, "AsyncClient", Client)

    assert not await wechat_mini_client.send_order_ready_subscribe(
        openid="wx-openid", order_no="SNH-2", order_id=2
    )
    token.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("temporary_error", [-1, 45011])
async def test_wechat_order_ready_retries_platform_temporary_errors_via_outbox(
    monkeypatch, temporary_error
):
    from types import SimpleNamespace

    from app.services import wechat_mini_client
    from app.services.system_settings_service import SystemSettingsService

    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(
            wechat_app_id="wx-app",
            wechat_app_secret="wx-secret",
            wechat_subscribe_template_order_ready="WX_READY",
        ),
    )
    monkeypatch.setattr(
        SystemSettingsService,
        "resolved_str_sync",
        staticmethod(lambda key, default="": "WX_READY"),
    )
    monkeypatch.setattr(
        wechat_mini_client, "get_access_token", AsyncMock(return_value="valid-token")
    )

    class Response:
        def json(self):
            return {"errcode": temporary_error, "errmsg": "temporarily unavailable"}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            return Response()

    monkeypatch.setattr(wechat_mini_client.httpx, "AsyncClient", Client)

    with pytest.raises(RuntimeError, match=f"errcode={temporary_error}"):
        await wechat_mini_client.send_order_ready_subscribe(
            openid="wx-openid", order_no="SNH-3", order_id=3
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_response",
    [
        [],
        {},
        {"errcode": None},
        {"errcode": "not-a-number"},
        {"errcode": True},
        {"errcode": 0.5},
    ],
)
async def test_wechat_order_ready_malformed_response_is_uncertain_not_retried(
    monkeypatch, malformed_response
):
    from types import SimpleNamespace

    from app.services import wechat_mini_client
    from app.services.outbox_worker import ExternalSideEffectUncertain
    from app.services.system_settings_service import SystemSettingsService

    monkeypatch.setattr(
        wechat_mini_client,
        "get_settings",
        lambda: SimpleNamespace(
            wechat_app_id="wx-app",
            wechat_app_secret="wx-secret",
            wechat_subscribe_template_order_ready="WX_READY",
        ),
    )
    monkeypatch.setattr(
        SystemSettingsService,
        "resolved_str_sync",
        staticmethod(lambda key, default="": "WX_READY"),
    )
    token = AsyncMock(return_value="valid-token")
    monkeypatch.setattr(wechat_mini_client, "get_access_token", token)
    requests = []

    class Response:
        def json(self):
            return malformed_response

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json):
            requests.append((url, json))
            return Response()

    monkeypatch.setattr(wechat_mini_client.httpx, "AsyncClient", Client)

    with pytest.raises(ExternalSideEffectUncertain, match="WECHAT"):
        await wechat_mini_client.send_order_ready_subscribe(
            openid="wx-openid", order_no="SNH-MALFORMED", order_id=99
        )

    token.assert_awaited_once()
    assert len(requests) == 1
