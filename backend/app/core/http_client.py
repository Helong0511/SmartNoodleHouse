"""共享 HTTP 客户端（连接池复用）"""
from __future__ import annotations

import asyncio

import httpx

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """获取共享 HTTP 客户端，双检锁防止并发创建多个实例。"""
    global _client
    if _client is None or _client.is_closed:
        async with _lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
                    follow_redirects=False,
                )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
