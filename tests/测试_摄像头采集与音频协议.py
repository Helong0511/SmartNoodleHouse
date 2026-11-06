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


def test_camera_manager_start_force_replaces_under_lock(monkeypatch):
    """同机位 force 启动须串行替换，最终只保留一个 worker。"""
    # 勿开真实摄像头/RTSP：仅验证 map 替换与锁外 join 序
    monkeypatch.setattr(CameraWorker, "_connect", lambda self: False)
    monkeypatch.setattr(
        CameraWorker,
        "_run",
        lambda self: self._stop.wait(timeout=30),
    )
    mgr = CameraManager()
    CameraManager.set_leader_check(None)
    for cid in (99101,):
        mgr.stop_camera(cid)
    w1 = mgr.start_camera(99101, "rtsp://192.0.2.1/x", fps=5)
    w2 = mgr.start_camera(99101, "rtsp://192.0.2.1/x", fps=5, force=True)
    assert mgr.get_worker(99101) is w2
    assert w1 is not w2
    mgr.stop_camera(99101)
    assert mgr.get_worker(99101) is None


def test_stop_camera_joins_outside_manager_lock():
    """stop 的 join 必须在全局注册表锁外执行，避免阻塞其他摄像头操作。"""
    import threading

    from camera import camera_manager as cm

    mgr = cm.CameraManager()
    camera_id = 99177
    stopped = []

    class Worker:
        _stop = threading.Event()

        def stop(self):
            # RLock 的 _is_owned 只用于测试断言：此处不能持有全局 _lock。
            assert not mgr._lock._is_owned()
            stopped.append(True)
            return True

    worker = Worker()
    with mgr._lock:
        mgr._workers[camera_id] = worker
    mgr.stop_camera(camera_id)
    assert stopped == [True]
    assert mgr.get_worker(camera_id) is None


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


def test_cameras_overview_leader_demotes_stale_online_without_worker():
    """Leader 无采集线程时勿把库内 ONLINE/DEGRADED 计入 KPI 在线。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api.cameras_overview)
    assert "is_leader_held" in src
    assert 'db_st in ("ONLINE", "DEGRADED", "RECONNECTING")' in src or (
        "ONLINE" in src and "DEGRADED" in src and "RECONNECTING" in src
    )


def test_serialize_leader_demotes_stale_online_without_worker():
    """列表/详情序列化与 overview 一致：Leader 无 worker 不展示库内 ONLINE。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api._serialize)
    assert "is_leader_held" in src
    assert 'db_st in ("ONLINE", "DEGRADED", "RECONNECTING")' in src


def test_bad_frame_with_recent_good_is_degraded():
    """坏帧但仍有近期好帧须标 DEGRADED（计入 usable），而非误标 ONLINE。"""
    w = CameraWorker(camera_id=99011, source="0", fps=5)
    good = np.zeros((48, 48, 3), dtype=np.uint8)
    good[:] = (40, 80, 120)
    w.frame_buffer.update(good)
    bad = np.zeros((48, 48, 3), dtype=np.uint8)
    w._apply_decode_result(bad, usable=False)
    assert w.status == "DEGRADED"
    assert w.frame_buffer.age_sec() is not None
    assert w.frame_buffer.age_sec() <= 5.0


def test_bad_frame_without_recent_good_is_reconnecting():
    """无近期好帧时坏帧须 RECONNECTING，不得标 DEGRADED 虚增 KPI。"""
    w = CameraWorker(camera_id=99012, source="0", fps=5)
    bad = np.zeros((48, 48, 3), dtype=np.uint8)
    w._apply_decode_result(bad, usable=False)
    assert w.status == "RECONNECTING"


def test_overview_uses_runtime_running_like_serialize():
    """overview 与 _serialize 均以线程存活为准，避免死线程 worker 虚增在线。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api.cameras_overview)
    assert "get_runtime" in src
    assert 'rt.get("running")' in src


def test_start_worker_maps_not_leader_to_app_exception():
    """非 Leader 启采须业务 409，而非未捕获 CameraNotLeaderError→500。"""
    from app.api.cameras import _start_worker
    from app.core.exceptions import AppException
    from camera.camera_manager import CameraManager

    CameraManager.set_leader_check(lambda: False)
    try:
        cam = MagicMock()
        cam.id = 99013
        cam.source_url = "rtsp://192.0.2.10/Streaming/Channels/101"
        cam.username = "admin"
        cam.password_encrypted = None
        cam.fps = 5
        with pytest.raises(AppException) as ei:
            _start_worker(cam)
        assert ei.value.code == 40922
        assert ei.value.status_code == 409
    finally:
        CameraManager.set_leader_check(None)


def test_create_camera_auto_start_swallows_not_leader():
    """创建 auto_start 遇非 Leader 须保留机位，不得整单 409 回滚。"""
    import inspect

    from app.api import cameras as cam_api

    src = inspect.getsource(cam_api.create_camera)
    assert "40922" in src
    assert "start_note" in src
    assert "not_leader" in src


def test_dead_worker_status_and_runtime_are_offline():
    """死线程残留不得继续报 ONLINE/has_frame；读路径须清理或短路。"""
    mgr = CameraManager()
    CameraManager.set_leader_check(None)
    cid = 99021
    mgr.stop_camera(cid)
    w = CameraWorker(camera_id=cid, source="0", fps=5)
    w.status = "ONLINE"
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    frame[:] = (10, 20, 30)
    w.frame_buffer.update(frame)
    # 模拟曾启动但已死的线程
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    w._thread = dead
    with mgr._lock:
        mgr._workers[cid] = w
    assert mgr.get_status(cid) == "OFFLINE"
    rt = mgr.get_runtime(cid)
    assert rt["running"] is False
    assert rt["status"] == "OFFLINE"
    assert rt["has_frame"] is False
    assert mgr.get_worker(cid) is None
    mgr.stop_camera(cid)


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


@pytest.mark.asyncio
async def test_play_g711_cancelled_after_open(monkeypatch):
    """open 往返后仍须认 abort，尽快 close 释锁。"""
    from app.services import hikvision_audio as ha

    client = ha.HikvisionTwoWayAudio("http://192.0.2.10", "admin", "pwd", timeout=3.0)
    calls = {"n": 0, "close": 0}

    async def _lock():
        class _L:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return _L()

    class _Resp:
        text = "<sessionId>1</sessionId>"

        def raise_for_status(self):
            return None

    class _Http:
        async def put(self, url):
            if url.endswith("/close"):
                calls["close"] += 1
                return _Resp()
            return _Resp()

    async def _shared(_timeout):
        return _Http()

    def _abort():
        calls["n"] += 1
        # 第一次 before_open 放行，第二次 after_open 取消
        return calls["n"] >= 2

    monkeypatch.setattr(client, "_lock_for", _lock)
    monkeypatch.setattr(client, "_shared_client", _shared)
    ha._SESSION_NEED_CLOSE.clear()
    out = await client.play_g711(
        b"\x00" * 80, codec="ulaw", should_abort=_abort
    )
    assert out["status"] == "cancelled"
    assert out["phase"] == "after_open"
    assert calls["close"] >= 1


@pytest.mark.asyncio
async def test_play_g711_abort_during_tail_hold(monkeypatch):
    """尾 hold 须可打断，勿 sleep(remain) 占满对讲锁。"""
    import inspect

    from app.services import hikvision_audio as ha

    src = inspect.getsource(ha.HikvisionTwoWayAudio.play_g711)
    assert 'abort_phase = "tail_hold"' in src or 'phase": "tail_hold"' in src
    assert "hold_deadline" in src
    assert "min(0.2, left)" in src


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


@pytest.mark.asyncio
async def test_play_on_camera_aborts_during_synth(monkeypatch):
    """合成阶段须认 should_abort，勿等到 play_g711 before_open。"""
    from types import SimpleNamespace

    from app.workers.tts_worker import CameraSpeakerTTS, LocalTTS

    local = MagicMock(spec=LocalTTS)
    local.synthesize_g711_sync = MagicMock(return_value=b"\xff" * 80)
    provider = CameraSpeakerTTS(local=local)

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _model, _id):
            return SimpleNamespace(
                source_url="rtsp://192.0.2.10/Streaming/Channels/101",
                username="admin",
                password_encrypted="enc",
                store_id=1,
            )

    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", lambda: _Sess()
    )
    monkeypatch.setattr(
        "app.services.camera_credentials.decrypt_password", lambda *_a, **_k: "pwd"
    )
    monkeypatch.setattr(
        "app.services.system_settings_service.SystemSettingsService.effective_int",
        AsyncMock(return_value=80),
    )

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def set_speaker_volume(self, volume):
            return True

        async def resolve_codec(self, block=False):
            return "alaw"

        async def play_g711(self, *a, **k):
            raise AssertionError("aborted before play")

    monkeypatch.setattr(
        "app.services.hikvision_audio.HikvisionTwoWayAudio", _Client
    )
    monkeypatch.setattr(
        "app.services.hikvision_audio.resolve_isapi_base",
        lambda *_a, **_k: "http://192.0.2.10",
    )
    monkeypatch.setattr(
        "app.services.hikvision_audio.normalize_g711_codec", lambda c: c
    )

    aborted = {"n": 0}

    def should_abort():
        aborted["n"] += 1
        return aborted["n"] >= 2  # 过 codec 后在合成完成检查处取消

    out = await provider._play_on_camera(
        "欢迎光临", 1, should_abort=should_abort
    )
    assert out == "cancelled"


@pytest.mark.asyncio
async def test_play_on_camera_aborts_mid_synth_wait(monkeypatch):
    """合成 await 中途须可 abort，勿等 executor 跑完。"""
    import time as _time
    from types import SimpleNamespace

    from app.workers.tts_worker import CameraSpeakerTTS, LocalTTS

    local = MagicMock(spec=LocalTTS)

    def _slow_synth(*_a, **_k):
        _time.sleep(1.2)
        return b"\xff" * 80

    local.synthesize_g711_sync = MagicMock(side_effect=_slow_synth)
    provider = CameraSpeakerTTS(local=local)

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, _model, _id):
            return SimpleNamespace(
                source_url="rtsp://192.0.2.10/Streaming/Channels/101",
                username="admin",
                password_encrypted="enc",
                store_id=1,
            )

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _Sess())
    monkeypatch.setattr(
        "app.services.camera_credentials.decrypt_password", lambda *_a, **_k: "pwd"
    )
    monkeypatch.setattr(
        "app.services.system_settings_service.SystemSettingsService.effective_int",
        AsyncMock(return_value=80),
    )

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def set_speaker_volume(self, volume):
            return True

        async def resolve_codec(self, block=False):
            return "alaw"

        async def play_g711(self, *a, **k):
            raise AssertionError("aborted before play")

    monkeypatch.setattr(
        "app.services.hikvision_audio.HikvisionTwoWayAudio", _Client
    )
    monkeypatch.setattr(
        "app.services.hikvision_audio.resolve_isapi_base",
        lambda *_a, **_k: "http://192.0.2.10",
    )
    monkeypatch.setattr(
        "app.services.hikvision_audio.normalize_g711_codec", lambda c: c
    )

    t0 = _time.perf_counter()
    flag = {"go": False}

    def should_abort():
        return flag["go"]

    async def _trip():
        await asyncio.sleep(0.25)
        flag["go"] = True

    trip = asyncio.create_task(_trip())
    out = await provider._play_on_camera(
        "欢迎光临", 1, should_abort=should_abort
    )
    await trip
    elapsed = _time.perf_counter() - t0
    assert out == "cancelled"
    assert elapsed < 1.0, f"mid-synth abort too slow: {elapsed:.2f}s"


def test_tts_local_outcome_marks_device_playback_local_only():
    """speak_outcome=local 不得标 unverified，以免假「对讲完成」。"""
    import inspect

    from app.workers.tts_worker import TTSWorker

    src = inspect.getsource(TTSWorker.run)
    assert 'local_only' in src
    assert 'speak_outcome == "local"' in src or "speak_outcome == 'local'" in src
