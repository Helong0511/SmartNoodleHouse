# -*- coding: utf-8 -*-
"""RTSP 帧龄、停止可打断、HTTPS 对讲拒绝、play_g711 结构化结果。"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera.camera_manager import CameraManager, CameraWorker, FrameBuffer


def test_camera_manager_start_force_replaces_under_lock():
    """同机位 force 启动须串行替换，最终只保留一个 worker。"""
    mgr = CameraManager()
    CameraManager.set_leader_check(None)
    # 清理可能残留的测试机位
    for cid in (99101,):
        mgr.stop_camera(cid)
    w1 = mgr.start_camera(99101, "0", fps=5)
    w2 = mgr.start_camera(99101, "0", fps=5, force=True)
    assert mgr.get_worker(99101) is w2
    assert w1 is not w2
    mgr.stop_camera(99101)
    assert mgr.get_worker(99101) is None


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


def test_stop_join_timeout_covers_rtsp_open_budget():
    """重启/停止 join 须覆盖 RTSP open 超时，避免旧线程仍占 VideoCapture。"""
    from camera import camera_manager as cm

    assert cm._STOP_JOIN_TIMEOUT_SEC >= cm._RTSP_OPEN_TIMEOUT_MS / 1000.0 + 3.0


def test_connect_aborts_when_stop_already_set():
    w = CameraWorker(camera_id=99002, source="rtsp://127.0.0.1:1/x", fps=5)
    w._stop.set()
    assert w._connect() is False
    assert w.status == "OFFLINE"
    assert w._cap is None


def test_cameras_overview_counts_degraded_as_usable():
    """概览 online 须计入 DEGRADED（与识别 Worker 一致），并透出 degraded 计数。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api.cameras_overview)
    assert 'st == "DEGRADED"' in src or "st == 'DEGRADED'" in src
    assert 'usable = online + degraded' in src
    assert '"degraded": degraded' in src


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


def _patch_audio_handshake(monkeypatch, status: int):
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
        ha, "_read_http_response", lambda *_a, **_k: (status, {}, b"")
    )
    monkeypatch.setattr(ha, "_build_digest_authorization", lambda **k: "Digest x")
    return ha


def test_stream_audio_rejects_http_status_zero(monkeypatch):
    """握手 status=0 不得当成功继续推流。"""
    ha = _patch_audio_handshake(monkeypatch, 0)
    with pytest.raises(RuntimeError, match="HTTP 0"):
        ha._stream_audio_data_content_length_zero(
            host="192.0.2.10",
            port=80,
            path="/ISAPI/System/TwoWayAudio/channels/1/audioData",
            username="admin",
            password="pwd",
            g711_data=b"\xff" * 64,
        )


def test_stream_audio_rejects_http_redirect(monkeypatch):
    """握手 302 等非 100/200 不得落穿推流。"""
    ha = _patch_audio_handshake(monkeypatch, 302)
    with pytest.raises(RuntimeError, match="HTTP 302"):
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
