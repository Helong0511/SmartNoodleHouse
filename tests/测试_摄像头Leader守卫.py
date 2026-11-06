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



def test_stop_all_cameras_signals_and_releases_every_worker():
    import threading

    class FakeThread:
        def __init__(self):
            self.alive = True
            self.join_calls = 0

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.join_calls += 1
            self.alive = False

    class FakeWorker:
        def __init__(self):
            self._stop = threading.Event()
            self._thread = FakeThread()
            self.status = "ONLINE"
            self.released = False

        def _release_cap(self):
            self.released = True

    manager = CameraManager()
    original = manager._workers
    old_stopping = manager._stopping_all
    first, second = FakeWorker(), FakeWorker()
    manager._workers = {99101: first, 99102: second}
    try:
        assert manager.stop_all_cameras(timeout=1) is True
        assert first._stop.is_set() and second._stop.is_set()
        assert first.released and second.released
        assert manager._workers == {}
    finally:
        manager._workers = original
        manager._stopping_all = old_stopping


def test_stop_all_cameras_keeps_worker_when_thread_does_not_exit():
    import threading

    class FakeThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    class FakeWorker:
        def __init__(self):
            self._stop = threading.Event()
            self._thread = FakeThread()
            self.status = "ONLINE"
            self.released = False

        def _release_cap(self):
            self.released = True

    manager = CameraManager()
    original = manager._workers
    old_stopping = manager._stopping_all
    worker = FakeWorker()
    manager._workers = {99103: worker}
    try:
        assert manager.stop_all_cameras(timeout=0.1) is False
        assert worker._stop.is_set()
        assert worker.released is False
        assert manager._workers.get(99103) is worker
    finally:
        manager._workers = original
        manager._stopping_all = old_stopping



def test_stop_camera_keeps_unstopped_worker_registered():
    import threading

    class FakeThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    class FakeWorker:
        def __init__(self):
            self._stop = threading.Event()
            self._thread = FakeThread()
            self.stopped = False

        def stop(self):
            self._stop.set()
            self.stopped = True
            return False

    manager = CameraManager()
    original = manager._workers
    worker = FakeWorker()
    manager._workers = {99104: worker}
    try:
        manager.stop_camera(99104)
        assert worker.stopped
        assert manager._workers.get(99104) is worker
    finally:
        manager._workers = original



def test_camera_manager_stays_closed_until_new_leader_resumes():
    manager = CameraManager()
    previous = manager._stopping_all
    CameraManager.set_leader_check(lambda: True)
    try:
        manager._stopping_all = True
        with pytest.raises(CameraNotLeaderError, match="正在停止"):
            manager._assert_leader()
        manager.resume_camera_manager()
        manager._assert_leader()
    finally:
        manager._stopping_all = previous
