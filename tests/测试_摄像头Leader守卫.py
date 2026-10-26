"""CameraManager Leader 守卫：类属性回调不得被 instance 绑定。"""
from __future__ import annotations

import pytest

from camera.camera_manager import CameraManager, CameraNotLeaderError


@pytest.fixture(autouse=True)
def _reset_leader_check():
    CameraManager.set_leader_check(None)
    yield
    CameraManager.set_leader_check(None)


def test_leader_check_free_function_not_bound_with_self():
    """回归：is_leader_held 无参；经 instance 调用不得传入 CameraManager。"""
    calls: list[tuple] = []

    def check(*args):
        calls.append(args)
        return True

    CameraManager.set_leader_check(check)
    CameraManager()._assert_leader()
    assert calls == [()], f"unexpected bind: {calls!r}"


def test_leader_check_false_raises():
    CameraManager.set_leader_check(lambda: False)
    with pytest.raises(CameraNotLeaderError):
        CameraManager()._assert_leader()


def test_leader_check_none_allows():
    CameraManager.set_leader_check(None)
    CameraManager()._assert_leader()


def test_api_start_worker_maps_not_leader():
    """API 层须把 CameraNotLeaderError 映射为 AppException 40922。"""
    from unittest.mock import MagicMock

    from app.api.cameras import _start_worker
    from app.core.exceptions import AppException

    CameraManager.set_leader_check(lambda: False)
    try:
        cam = MagicMock()
        cam.id = 99199
        cam.source_url = "rtsp://192.0.2.20/Streaming/Channels/101"
        cam.username = "admin"
        cam.password_encrypted = None
        cam.fps = 5
        with pytest.raises(AppException) as ei:
            _start_worker(cam)
        assert ei.value.code == 40922
    finally:
        CameraManager.set_leader_check(None)
