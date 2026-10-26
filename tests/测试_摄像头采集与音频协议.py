# -*- coding: utf-8 -*-
"""RTSP 帧龄、停止可打断、HTTPS 对讲拒绝、play_g711 结构化结果。"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera.camera_manager import CameraWorker, FrameBuffer


def test_frame_buffer_age_uses_monotonic():
    buf = FrameBuffer()
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    buf.update(frame)
    age1 = buf.age_sec()
    assert age1 is not None and age1 < 0.5
    time.sleep(0.05)
    age2 = buf.age_sec()
    assert age2 is not None and age2 >= age1


def test_camera_worker_stop_interrupts_reconnect_sleep():
    w = CameraWorker(camera_id=99001, source="rtsp://127.0.0.1:1/x", fps=25)
    # 跳过真实 open，直接失败进入可打断重连等待
    w._connect = lambda: False  # type: ignore[method-assign]
    w._reconnect_delay = 30
    w._max_reconnect_delay = 30
    w.start()
    time.sleep(0.15)
    t0 = time.perf_counter()
    w.stop()
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"stop blocked {elapsed:.2f}s"
    assert not (w._thread and w._thread.is_alive())


@pytest.mark.asyncio
async def test_play_g711_rejects_https_base():
    from app.services.hikvision_audio import HikvisionTwoWayAudio

    client = HikvisionTwoWayAudio(
        "https://192.0.2.10", "admin", "pwd", timeout=3.0
    )
    out = await client.play_g711(b"\x00" * 80, codec="ulaw")
    assert out["status"] == "failed"
    assert out["error"] == "unsupported_audio_transport"
    assert out["device_playback"] == "unverified"


@pytest.mark.asyncio
async def test_play_g711_cancelled_before_open(monkeypatch):
    from app.services import hikvision_audio as ha

    client = ha.HikvisionTwoWayAudio("http://192.0.2.10", "admin", "pwd", timeout=3.0)

    async def _lock():
        class _L:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return _L()

    monkeypatch.setattr(client, "_lock_for", _lock)
    out = await client.play_g711(
        b"\x00" * 80, codec="ulaw", should_abort=lambda: True
    )
    assert out["status"] == "cancelled"
    assert out["phase"] == "before_open"


def test_stream_audio_rejects_http_status_zero(monkeypatch):
    """握手 status=0 不得当成功继续推流。"""
    from app.services import hikvision_audio as ha

    class _Sock:
        def sendall(self, *_a, **_k):
            return None

        def settimeout(self, *_a, **_k):
            return None

        def close(self):
            return None

        def shutdown(self, *_a, **_k):
            return None

    monkeypatch.setattr(
        ha.socket, "create_connection", lambda *a, **k: _Sock()
    )
    monkeypatch.setattr(
        ha, "_read_http_response", lambda *_a, **_k: (0, {}, b"")
    )
    monkeypatch.setattr(ha, "_build_digest_authorization", lambda **k: "Digest x")
    with pytest.raises(RuntimeError, match="status=0"):
        ha._stream_audio_data_content_length_zero(
            host="192.0.2.10",
            port=80,
            path="/ISAPI/System/TwoWayAudio/channels/1/audioData",
            username="admin",
            password="pwd",
            g711_data=b"\xff" * 64,
        )


@pytest.mark.asyncio
async def test_camera_speaker_speak_returns_cancelled(monkeypatch):
    from app.workers.tts_worker import CameraSpeakerTTS, LocalTTS

    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)
    monkeypatch.setattr(
        provider, "_camera_speaker_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        provider, "_play_on_camera", AsyncMock(return_value="cancelled")
    )
    out = await provider.speak("欢迎光临", camera_id=1)
    assert out == "cancelled"
    local.speak.assert_not_awaited()
