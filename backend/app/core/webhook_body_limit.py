"""Bounded request-body middleware for payment/provider callbacks."""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)
_ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class WebhookBodyLimitMiddleware:
    """Buffer callback bodies only up to a strict byte ceiling, then replay once."""

    def __init__(self, app: _ASGIApp, *, max_body_bytes: int = 512 * 1024) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")

    @staticmethod
    def _is_limited_path(path: str) -> bool:
        lowered = path.lower()
        return any(part in lowered for part in ("/webhook", "/notify", "/callback"))

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[Any]], status: int, message: str) -> None:
        body = json.dumps({"code": status, "message": message}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope.get("type") != "http" or not self._is_limited_path(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return

        raw_lengths = [value.strip() for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if len(raw_lengths) > 1:
            await self._reject(send, 400, "Invalid Content-Length")
            return
        declared_length: int | None = None
        if raw_lengths:
            raw = raw_lengths[0]
            if not raw or not raw.isdigit():
                await self._reject(send, 400, "Invalid Content-Length")
                return
            try:
                declared_length = int(raw)
            except (ValueError, OverflowError):
                await self._reject(send, 400, "Invalid Content-Length")
                return
            if declared_length > self.max_body_bytes:
                await self._reject(send, 413, "Webhook body too large")
                return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            if not isinstance(chunk, bytes):
                await self._reject(send, 400, "Invalid request body")
                return
            total += len(chunk)
            if total > self.max_body_bytes:
                logger.warning("Rejected oversized callback body path=%s bytes>%s", scope.get("path"), self.max_body_bytes)
                await self._reject(send, 413, "Webhook body too large")
                return
            if chunk:
                chunks.append(chunk)
            if not message.get("more_body", False):
                break

        if declared_length is not None and declared_length != total:
            await self._reject(send, 400, "Content-Length mismatch")
            return

        body = b"".join(chunks)
        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)
