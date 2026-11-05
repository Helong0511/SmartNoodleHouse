from __future__ import annotations

import pytest

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

    sent = await _run(app, "/api/payment/webhook", [
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

    sent = await _run(app, "/api/pay/notify", [
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

    sent = await _run(app, "/callback/payment", [], headers=[(b"content-length", value)])
    assert not called
    assert sent[0]["status"] == 400


@pytest.mark.asyncio
async def test_declared_oversize_rejected_before_reading_body():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = await _run(app, "/webhook/provider", [], headers=[(b"content-length", b"9")])
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

    sent = await _run(app, "/webhook/provider", [], headers=[
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

    sent = await _run(app, "/notify/provider", [
        {"type": "http.request", "body": b"abc", "more_body": False},
    ], headers=[(b"content-length", b"4")])
    assert not called
    assert sent[0]["status"] == 400
