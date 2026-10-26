"""门口迎宾：短时窗口择优（近、清晰优先于远景小人脸）。"""
from __future__ import annotations

import numpy as np

from app.services.face_best_frame import BestFaceWindow, LiveFaceHit


def _hit(*, ratio: float, sharp: float = 50.0, det: float = 0.6) -> LiveFaceHit:
    emb = np.ones(512, dtype=np.float32)
    emb = emb / (np.linalg.norm(emb) + 1e-8)
    return LiveFaceHit(
        embedding=emb,
        det_score=det,
        face_area_ratio=ratio,
        sharpness=sharp,
    )


def test_score_prefers_closer_face():
    far = _hit(ratio=0.01, sharp=80.0, det=0.9)
    near = _hit(ratio=0.08, sharp=40.0, det=0.5)
    assert BestFaceWindow.score(near) > BestFaceWindow.score(far)


def test_window_waits_then_picks_best_not_first():
    win = BestFaceWindow(
        window_sec=2.8,
        max_wait_sec=5.0,
        early_ratio=0.12,
        early_sharpness=200.0,
        early_det_score=0.9,
    )
    frame_far = np.zeros((64, 64, 3), dtype=np.uint8)
    frame_near = np.ones((64, 64, 3), dtype=np.uint8) * 7

    assert win.push(_hit(ratio=0.012, sharp=40.0, det=0.4), frame=frame_far, now=100.0) is None
    assert win.push(_hit(ratio=0.02, sharp=40.0, det=0.4), frame=frame_far, now=100.8) is None
    decided = win.push(_hit(ratio=0.09, sharp=55.0, det=0.4), frame=frame_near, now=103.0)
    assert decided is not None
    hit, fr, support = decided
    assert hit.face_area_ratio == 0.09
    assert fr is not None
    assert int(fr[0, 0, 0]) == 7
    assert len(support) >= 1
    assert win.empty()


def test_early_decide_when_face_already_large_and_sharp():
    win = BestFaceWindow()
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    # 对齐现场清晰正对：ratio≈0.01 det≈0.86 sharp≈1100 应立刻定稿
    decided = win.push(
        _hit(ratio=0.01, sharp=200.0, det=0.86),
        frame=frame,
        now=50.0,
    )
    assert decided is not None
    assert decided[0].face_area_ratio == 0.01
    assert isinstance(decided[2], list)


def test_tiny_far_face_waits_then_soft_decide_for_match():
    """由远走近：max_wait 前不定稿；满窗后可定稿供试匹配（访客侧另有 ratio 守卫）。"""
    win = BestFaceWindow(
        window_sec=2.0,
        max_wait_sec=3.0,
        hard_abandon_sec=9.0,
        min_decide_ratio=0.006,
        early_ratio=0.05,
        early_sharpness=200.0,
        plateau_min_ratio=0.05,
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert win.push(_hit(ratio=0.0002, sharp=8000.0, det=0.35), frame=frame, now=0.0) is None
    assert win.push(_hit(ratio=0.0003, sharp=5000.0, det=0.4), frame=frame, now=2.5) is None
    soft = win.push(_hit(ratio=0.0003, sharp=5000.0, det=0.4), frame=frame, now=3.05)
    assert soft is not None
    assert soft[0].face_area_ratio < 0.006


def test_far_face_hard_abandon_clears_without_decide():
    win = BestFaceWindow(max_wait_sec=3.0, hard_abandon_sec=5.0, min_decide_ratio=0.006)
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert win.push(_hit(ratio=0.0002, sharp=8000.0, det=0.3), frame=frame, now=10.0) is None
    assert win.push(_hit(ratio=0.0003, sharp=7000.0, det=0.3), frame=frame, now=15.5) is None
    assert win.empty()


def test_closer_face_decides_before_max_wait():
    win = BestFaceWindow(
        window_sec=2.0,
        max_wait_sec=5.0,
        min_decide_ratio=0.006,
        early_ratio=0.008,
        early_sharpness=80.0,
        early_det_score=0.55,
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert win.push(_hit(ratio=0.0002, sharp=8000.0, det=0.35), frame=frame, now=0.0) is None
    decided = win.push(_hit(ratio=0.01, sharp=200.0, det=0.86), frame=frame, now=1.2)
    assert decided is not None
    assert decided[0].face_area_ratio == 0.01


def test_plateau_decides_mid_distance():
    win = BestFaceWindow(plateau_sec=0.6, plateau_min_ratio=0.038, early_ratio=0.05)
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert win.push(_hit(ratio=0.045, sharp=100.0, det=0.5), frame=frame, now=10.0) is None
    decided = win.push(_hit(ratio=0.045, sharp=100.0, det=0.5), frame=frame, now=10.75)
    assert decided is not None
    assert decided[0].face_area_ratio >= 0.045
    assert len(decided[2]) >= 1


def test_prune_now_keeps_waiting_on_weak_face():
    win = BestFaceWindow(
        max_wait_sec=2.0, hard_abandon_sec=8.0, min_decide_ratio=0.006, plateau_min_ratio=0.007
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert win.push(_hit(ratio=0.003, sharp=40.0, det=0.4), frame=frame, now=1.0) is None
    assert win.prune_now(now=4.0) is None
    assert not win.empty()
    assert win.prune_now(now=10.0) is None
    assert win.empty()
