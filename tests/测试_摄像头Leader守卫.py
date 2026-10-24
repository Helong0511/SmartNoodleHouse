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
