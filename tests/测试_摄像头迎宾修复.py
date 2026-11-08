# -*- coding: utf-8 -*-
"""摄像头迎宾 P0/P1 修复回归：codec 不伪造成功、改密清缓存、冷却回滚。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import app.services.hikvision_audio as ha
from app.services.face_service import WelcomeService
from app.services.hikvision_audio import (
    HikvisionTwoWayAudio,
    invalidate_hikvision_caches,
)


@pytest_asyncio.fixture(autouse=True)
async def _clear_hik_caches():
    # asyncio.Lock / AsyncClient 按事件循环隔离；在 async fixture 中清理当前 loop 状态。
    state = ha._get_loop_state()
    for client in list(state.shared_http.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    state.shared_http.clear()
    state.codec_detect_pending.clear()
    ha._CODEC_CACHE.clear()
    ha._SESSION_NEED_CLOSE.clear()
    ha._DIGEST_WWW_CACHE.clear()
    yield
    state = ha._get_loop_state()
    for client in list(state.shared_http.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    state.shared_http.clear()
    state.codec_detect_pending.clear()
    ha._CODEC_CACHE.clear()
    ha._SESSION_NEED_CLOSE.clear()
    ha._DIGEST_WWW_CACHE.clear()


@pytest.mark.asyncio
async def test_detect_codec_failure_does_not_persist_default():
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=500, text="err"))
    audio = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "pass")
    codec = await audio._detect_codec(client)
    assert codec == "alaw"
    assert ha._CODEC_CACHE.get(audio._device_key()) is None


@pytest.mark.asyncio
async def test_resolve_codec_nonblocking_does_not_poison_cache():
    audio = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "pass")
    with patch.object(audio, "_shared_client", new=AsyncMock(side_effect=RuntimeError("no net"))):
        c = await audio.resolve_codec(block=False)
    assert c == "alaw"
    assert ha._CODEC_CACHE.get(audio._device_key()) is None


@pytest.mark.asyncio
async def test_shared_client_key_changes_with_password_and_invalidate():
    a1 = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "old")
    a2 = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "new")
    timeout = MagicMock()
    c1 = await a1._shared_client(timeout)
    c2 = await a2._shared_client(timeout)
    assert c1 is not c2
    assert len(ha._get_loop_state().shared_http) == 2
    await invalidate_hikvision_caches(base_url="http://192.168.1.64", channel=1)
    assert len(ha._get_loop_state().shared_http) == 0


@pytest.mark.asyncio
async def test_try_welcome_releases_cooldown_when_customer_missing():
    deleted: list[str] = []

    class _Redis:
        async def set(self, *a, **k):
            return True

        async def delete(self, key):
            deleted.append(key)
            return 1

    db = MagicMock()
    db.get = AsyncMock(return_value=None)
    with (
        patch.object(WelcomeService, "try_acquire_cooldown", new=AsyncMock(return_value=True)),
        patch.object(WelcomeService, "push_frame_score", new=AsyncMock(return_value=(True, 0.9))),
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_float",
            new=AsyncMock(return_value=0.48),
        ),
        patch("app.services.face_service.get_redis", new=AsyncMock(return_value=_Redis())),
    ):
        ok = await WelcomeService.try_welcome(db, 999001, 1, 0.9, preconfirmed=True)
    assert ok is False
    assert deleted and any("cooldown" in k for k in deleted)
