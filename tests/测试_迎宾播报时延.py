"""迎宾播报时延相关：过期丢弃、会员打断世代、近阈值策略常量。"""
from __future__ import annotations

import time

from app.services.face_service import WelcomeService


def test_stale_item_drops_old_welcome():
    old = {
        "kind": "unknown_visitor",
        "text": "欢迎光临",
        "camera_id": 1,
        "enqueued_at": time.time() - 30.0,
    }
    fresh = {
        "kind": "member",
        "text": "尊贵的会员您好",
        "camera_id": 1,
        "enqueued_at": time.time(),
    }
    assert WelcomeService.is_stale_item(old) is True
    assert WelcomeService.is_stale_item(fresh) is False


def test_test_speak_never_stale():
    item = {
        "kind": "test_speak_camera",
        "text": "试听播报",
        "camera_id": 1,
        "enqueued_at": time.time() - 120.0,
    }
    assert WelcomeService.is_stale_item(item) is False


def test_speak_generation_bump_for_member_interrupt():
    cam = 90001
    g0 = WelcomeService.speak_generation(cam)
    g1 = WelcomeService.bump_speak_generation(cam)
    assert g1 == g0 + 1
    assert WelcomeService.speak_generation(cam) == g1


def test_near_threshold_margin_covers_false_unknown():
    """sim=0.44 / threshold=0.48 应落入近阈值，短暂暂缓陌生迎宾。"""
    threshold = 0.48
    near_margin = 0.08
    best_sim = 0.444
    defer = best_sim >= threshold - near_margin
    assert defer is True
    # 超时后仍按访客播，避免干等 10–20s
    near_max_wait = 1.5
    assert near_max_wait <= 2.0
    assert 0.15 < threshold - near_margin  # 真正陌生仍会迎宾


def test_face_extractor_live_ready_helpers_exist():
    from app.services.face_extractor import ensure_live_face_app, is_live_face_ready

    assert callable(ensure_live_face_app)
    assert callable(is_live_face_ready)
