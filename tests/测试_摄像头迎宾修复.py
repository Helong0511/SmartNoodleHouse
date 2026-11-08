# -*- coding: utf-8 -*-
"""摄像头迎宾 P0/P1 修复回归：codec 不伪造成功、改密清缓存、冷却回滚。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.hikvision_audio as ha
from app.services.face_service import WelcomeService
from app.services.hikvision_audio import (
    HikvisionTwoWayAudio,
    invalidate_hikvision_caches,
)


@pytest.fixture(autouse=True)
def _clear_hik_caches():
    ha._SHARED_HTTP.clear()
    ha._CODEC_CACHE.clear()
    ha._CODEC_DETECT_PENDING.clear()
    ha._SESSION_NEED_CLOSE.clear()
    ha._DIGEST_WWW_CACHE.clear()
    yield
    ha._SHARED_HTTP.clear()
    ha._CODEC_CACHE.clear()
    ha._CODEC_DETECT_PENDING.clear()
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
    assert len(ha._SHARED_HTTP) == 2
    await invalidate_hikvision_caches(base_url="http://192.168.1.64", channel=1)
    assert len(ha._SHARED_HTTP) == 0


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
