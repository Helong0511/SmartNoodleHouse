from __future__ import annotations

import asyncio

import pytest

from app.core import webhook_body_limit
from app.core.webhook_body_limit import WebhookBodyLimitMiddleware


def _scope(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": path,
            "headers": headers or [], "query_string": b"", "server": ("test", 80),
            "client": ("127.0.0.1", 12345), "scheme": "http", "http_version": "1.1"}


async def _run(app, path, messages, headers=None, limit=8):
    sent = []
    queue = list(messages)

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = WebhookBodyLimitMiddleware(app, max_body_bytes=limit)
    await middleware(_scope(path, headers), receive, send)
    return sent


@pytest.mark.asyncio
async def test_chunked_webhook_over_limit_is_rejected_without_calling_app():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/api/payments/wechat/notify", [
        {"type": "http.request", "body": b"12345", "more_body": True},
        {"type": "http.request", "body": b"67890", "more_body": False},
    ])
    assert not called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_valid_split_body_is_replayed_exactly_once():
    received = []

    async def app(scope, receive, send):
        first = await receive()
        second = await receive()
        received.extend([first, second])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = await _run(app, "/api/payments/alipay/notify", [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"def", "more_body": False},
    ], headers=[(b"content-length", b"6")])
    assert received[0] == {"type": "http.request", "body": b"abcdef", "more_body": False}
    assert received[1]["type"] == "http.disconnect"
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"-1", b"abc", b"9" * 5000])
async def test_invalid_content_length_returns_400(value):
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/api/payments/wechat/refund-notify", [], headers=[(b"content-length", value)])
    assert not called
    assert sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_declared_oversize_rejected_before_reading_body():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/api/delivery/meituan/notify", [], headers=[(b"content-length", b"9")])
    assert not called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_non_callback_path_is_passed_through_without_buffering():
    observed = []

    async def app(scope, receive, send):
        observed.append(await receive())

    sent = await _run(app, "/api/orders", [
        {"type": "http.request", "body": b"unbounded-here", "more_body": False},
    ])
    assert observed[0]["body"] == b"unbounded-here"
    assert sent == []


@pytest.mark.asyncio
async def test_duplicate_content_length_headers_are_rejected():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/api/delivery/meituan/notify", [], headers=[
        (b"content-length", b"1"), (b"content-length", b"1"),
    ])
    assert not called
    assert sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_content_length_mismatch_is_rejected():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/api/delivery/eleme/notify", [
        {"type": "http.request", "body": b"abc", "more_body": False},
    ], headers=[(b"content-length", b"4")])
    assert not called
    assert sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_slow_callback_body_is_rejected_after_total_deadline(monkeypatch):
    monkeypatch.setattr(webhook_body_limit, "_CALLBACK_BODY_TOTAL_TIMEOUT_SEC", 0.01)
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        await asyncio.Future()

    sent = []

    async def send(message):
        sent.append(message)

    middleware = WebhookBodyLimitMiddleware(app, max_body_bytes=8)
    await middleware(_scope("/api/delivery/meituan/notify"), receive, send)
    assert not called
    assert sent[0]["status"] == 408


@pytest.mark.asyncio
async def test_non_webhook_path_containing_callback_is_not_buffered():
    observed = []

    async def app(scope, receive, send):
        observed.append(await receive())

    sent = await _run(app, "/api/orders/callback", [
        {"type": "http.request", "body": b"normal-order", "more_body": False},
    ])
    assert observed[0]["body"] == b"normal-order"
    assert sent == []
