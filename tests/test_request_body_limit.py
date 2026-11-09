"""Global request-body limit regression tests."""
import pytest

from app.core.request_body_limit import RequestBodyLimitMiddleware


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_app():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "path": "/api/orders", "headers": [(b"content-length", b"6")]},
        receive,
        send,
    )
    assert not called
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_chunked_oversized_body_is_rejected_while_streaming():
    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    messages = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": True},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await middleware({"type": "http", "path": "/api/orders", "headers": []}, receive, send)
    assert sent[0]["status"] == 413
    assert not any(m.get("status") == 200 for m in sent)
