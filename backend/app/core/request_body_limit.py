"""全局 ASGI 请求体硬限制，兼容 Content-Length 与 chunked 请求。"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

_ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class RequestBodyLimitMiddleware:
    """不缓存请求体，边读取边计数；超限时直接返回 413。"""

    def __init__(self, app: _ASGIApp, *, max_body_bytes: int = 7 * 1024 * 1024) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[Any]], status: int, message: str) -> None:
        body = json.dumps(
            {"code": status, "message": message},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_lengths = [
            value.strip()
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if len(raw_lengths) > 1:
            await self._reject(send, 400, "Invalid Content-Length")
            return
        if raw_lengths:
            raw = raw_lengths[0]
            if not raw or not raw.isdigit():
                await self._reject(send, 400, "Invalid Content-Length")
                return
            if int(raw) > self.max_body_bytes:
                await self._reject(send, 413, "Request body too large")
                return

        total = 0
        rejected = False
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal total, rejected
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b"") or b"")
                if total > self.max_body_bytes:
                    rejected = True
                    if not response_started:
                        await self._reject(send, 413, "Request body too large")
                    return {"type": "http.disconnect"}
            return message

        async def limited_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if rejected:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            # 请求体超限后，下游可能因 disconnect 抛出 ClientDisconnect；413 已由本中间件发送。
            if not rejected:
                raise
