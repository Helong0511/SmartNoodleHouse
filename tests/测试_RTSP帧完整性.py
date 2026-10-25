"""过滤 HEVC 半解码花屏帧。"""
from __future__ import annotations

import numpy as np

from camera.camera_manager import is_rtsp_frame_usable


def test_normal_office_like_frame_usable():
    rng = np.random.default_rng(0)
    frame = rng.integers(40, 200, size=(480, 640, 3), dtype=np.uint8)
    assert is_rtsp_frame_usable(frame) is True


def test_flat_grey_frame_rejected():
    frame = np.full((480, 640, 3), 114, dtype=np.uint8)
    assert is_rtsp_frame_usable(frame) is False


def test_half_grey_strip_rejected():
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    # 右侧一条有内容，模拟用户第二张「左灰右碎」花屏
    frame[:, 500:640] = np.random.default_rng(1).integers(20, 220, size=(480, 140, 3), dtype=np.uint8)
    assert is_rtsp_frame_usable(frame) is False
