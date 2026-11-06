from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.exceptions import AppException
from app.utils import upload


class _FakeNetworkStream:
    def __init__(self, peer):
        self.peer = peer

    def get_extra_info(self, name):
        return self.peer if name == "peername" else None


class _FakeResponse:
    def __init__(self, peer):
        self.status_code = 200
        self.headers = {}
        self.extensions = {"network_stream": _FakeNetworkStream(peer)}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aiter_bytes(self, chunk_size):
        yield b"\xff\xd8\xfffake-jpeg"


class _FakeClient:
    def __init__(self, peer, **kwargs):
        self.peer = peer
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url, headers=None):
        return _FakeResponse(self.peer)


@pytest.mark.asyncio
async def test_remote_image_fetch_disables_environment_proxy_and_checks_socket_peer(monkeypatch):
    created = {}

    def fake_client(**kwargs):
        created.update(kwargs)
        return _FakeClient(("8.8.8.8", 443), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(upload, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(upload, "_max_upload_mb", lambda: 1)
    monkeypatch.setattr(upload, "sniff_image_type", lambda data: ("jpg", "image/jpeg"))
    monkeypatch.setattr(upload, "save_image_bytes", AsyncMock(return_value="/uploads/avatars/a.webp"))

    result = await upload.fetch_and_save_remote_image("https://cdn.example/avatar.jpg")

    assert result == "/uploads/avatars/a.webp"
    assert created["trust_env"] is False
    assert created["follow_redirects"] is False


@pytest.mark.asyncio
async def test_remote_image_fetch_rejects_missing_socket_peer(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(None, **kwargs))
    monkeypatch.setattr(upload, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(upload, "_max_upload_mb", lambda: 1)

    with pytest.raises(AppException, match="目标IP"):
        await upload.fetch_and_save_remote_image("https://cdn.example/avatar.jpg")


@pytest.mark.asyncio
async def test_remote_image_fetch_rejects_private_socket_peer(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(("127.0.0.1", 80), **kwargs))
    monkeypatch.setattr(upload, "assert_public_http_url", lambda url: None)
    monkeypatch.setattr(upload, "_max_upload_mb", lambda: 1)

    with pytest.raises(AppException, match="不允许拉取"):
        await upload.fetch_and_save_remote_image("https://cdn.example/avatar.jpg")
