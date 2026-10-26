# -*- coding: utf-8 -*-
"""多帧近重复不计入确认；播报 task_id 状态可读写。"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.face_extractor import EMBEDDING_DIM
from app.services.face_liveness import MultiFrameTracker


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def test_multiframe_rejects_near_duplicate_embeddings():
    tr = MultiFrameTracker(confirm_frames=2, ttl_seconds=5.0, duplicate_cos=0.995)
    emb = _unit(7)
    ok1, cid1, _ = tr.push(1, emb, candidate_id=42, similarity=0.9)
    assert ok1 is False
    # 同一向量再推：不得确认
    ok2, cid2, _ = tr.push(1, emb.copy(), candidate_id=42, similarity=0.91)
    assert ok2 is False
    assert cid2 == 42
    # 明显不同的第二帧才确认
    ok3, cid3, avg = tr.push(1, _unit(8), candidate_id=42, similarity=0.88)
    assert ok3 is True
    assert cid3 == 42
    assert avg >= 0.88


@pytest.mark.asyncio
async def test_speak_task_status_roundtrip(monkeypatch):
    from app.services.face_service import WelcomeService

    store: dict[str, str] = {}

    class _FakeRedis:
        async def set(self, key, value, ex=None):
            store[key] = value
            return True

        async def get(self, key):
            return store.get(key)

    async def _fake_get_redis():
        return _FakeRedis()

    monkeypatch.setattr("app.services.face_service.get_redis", _fake_get_redis)
    tid = "abc123task"
    await WelcomeService.set_speak_task_status(
        tid, "queued", camera_id=9, kind="test_speak_camera"
    )
    data = await WelcomeService.get_speak_task(tid)
    assert data is not None
    assert data["status"] == "queued"
    assert data["camera_id"] == 9
    await WelcomeService.set_speak_task_status(
        tid, "completed", device_playback="unverified"
    )
    data2 = await WelcomeService.get_speak_task(tid)
    assert data2["status"] == "completed"
    assert data2["device_playback"] == "unverified"
