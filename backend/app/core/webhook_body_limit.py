"""Bounded request-body middleware for payment/provider callbacks."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)
_ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]
_MAX_CALLBACK_BUFFER_BUDGET_BYTES = 16 * 1024 * 1024
_MAX_CONCURRENT_CALLBACKS = 32
_CALLBACK_SLOT_WAIT_SEC = 0.5
_CALLBACK_BODY_TOTAL_TIMEOUT_SEC = 15.0


class WebhookBodyLimitMiddleware:
    """Bound callback body bytes and concurrent buffering, then replay body once."""

    def __init__(self, app: _ASGIApp, *, max_body_bytes: int = 512 * 1024) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_bytes)
        if self.max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        # Bound aggregate in-memory callback payload per process, not only request count.
        # At the default 512KB limit this permits 32 callbacks; at 10MB it permits one.
        # Joining chunks may temporarily double payload memory, so plan for ~2x this budget.
        slots = max(1, min(
            _MAX_CONCURRENT_CALLBACKS,
            _MAX_CALLBACK_BUFFER_BUDGET_BYTES // self.max_body_bytes,
        ))
        self._callback_slots = asyncio.Semaphore(slots)

    @staticmethod
    def _is_limited_path(path: str) -> bool:
        # 与 WebhookAllowlistMiddleware 共用唯一的回调路径清单，避免
        # substring 误匹配普通业务 API，或新回调只加了大小限制却漏掉 IP 白名单。
        from app.core.ip_allowlist import WEBHOOK_PATHS

        return path.rstrip("/") in WEBHOOK_PATHS

    @staticmethod
    async def _reject(send: Callable[..., Awaitable[Any]], status: int, message: str) -> None:
        body = json.dumps({"code": status, "message": message}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"retry-after", b"1") if status == 503 else (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
        if scope.get("type") != "http" or not self._is_limited_path(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self._callback_slots.acquire(), timeout=_CALLBACK_SLOT_WAIT_SEC)
        except asyncio.TimeoutError:
            await self._reject(send, 503, "Webhook capacity temporarily exhausted")
            return
        try:
            await self._handle_callback(scope, receive, send)
        finally:
            self._callback_slots.release()

    async def _handle_callback(self, scope: dict[str, Any], receive: Callable[..., Awaitable[Any]], send: Callable[..., Awaitable[Any]]) -> None:
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
        deadline = asyncio.get_running_loop().time() + _CALLBACK_BODY_TOTAL_TIMEOUT_SEC
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self._reject(send, 408, "Webhook body read timeout")
                return
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                await self._reject(send, 408, "Webhook body read timeout")
                return
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
