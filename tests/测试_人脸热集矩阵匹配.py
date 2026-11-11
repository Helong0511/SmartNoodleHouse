# -*- coding: utf-8 -*-
"""热集矩阵匹配：预归一化点积与冷匹配不持全局锁。"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.face_extractor import EMBEDDING_DIM
from app.services.face_service import FaceEmbeddingCache, FaceService


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-8)


def test_match_hot_matrix_agrees_with_cosine_loop():
    cache = FaceEmbeddingCache()
    target = _unit(1)
    cache._by_customer = {
        10: (1, target),
        20: (2, _unit(2)),
        30: (3, _unit(3)),
    }
    cache._matrix_dirty = True
    mid, msim = cache._match_hot(target)
    assert mid == 10
    assert msim > 0.99

    # 与逐条 cosine 一致
    best_id, best_sim = None, -1.0
    for cid, (_pid, vec) in cache._by_customer.items():
        s = FaceService.cosine_similarity(target, vec)
        if s > best_sim:
            best_id, best_sim = cid, s
    assert mid == best_id
    assert abs(msim - best_sim) < 1e-5


def test_match_hot_matrix_faster_than_python_renorm_loop_at_scale():
    cache = FaceEmbeddingCache()
    n = 800
    cache._by_customer = {i + 1: (i + 1, _unit(i + 1)) for i in range(n)}
    cache._rebuild_matrix()
    q = cache._by_customer[1][1].copy()

    t0 = time.perf_counter()
    for _ in range(40):
        mid, _ = cache._match_hot(q)
    t_mat = (time.perf_counter() - t0) / 40 * 1000
    assert mid == 1

    t0 = time.perf_counter()
    best_id = None
    for _ in range(40):
        best_id, best_sim = None, -1.0
        for cid, (_pid, vec) in cache._by_customer.items():
            s = FaceService.cosine_similarity(q, vec)
            if s > best_sim:
                best_id, best_sim = cid, s
    t_loop = (time.perf_counter() - t0) / 40 * 1000
    assert best_id == 1
    assert t_mat < t_loop * 0.5, f"matrix={t_mat:.3f}ms loop={t_loop:.3f}ms"


@pytest.mark.asyncio
async def test_best_match_releases_lock_during_cold_match(monkeypatch):
    monkeypatch.setattr("app.services.face_service.FACE_RECOGNITION_TEMPORARILY_DISABLED", False)
    cache = FaceEmbeddingCache()
    cache._by_customer = {1: (1, _unit(1))}
    cache._matrix_dirty = True

    lock_held_during_cold = False
    original_cold = cache._cold_match

    async def _cold(db, emb, threshold, hot_ids):
        nonlocal lock_held_during_cold
        lock_held_during_cold = cache._lock.locked()
        return None, 0.0

    cache._cold_match = _cold  # type: ignore[method-assign]
    cache._refresh = AsyncMock()  # type: ignore[method-assign]

    db = MagicMock()
    # 热集无命中 → 走冷库
    miss = _unit(99)
    await cache.best_match(db, miss, threshold=0.99, camera_id=7)
    assert lock_held_during_cold is False
    cache._cold_match = original_cold  # type: ignore[method-assign]


def test_decrypt_vec_normalizes():
    enc = MagicMock()
    # 非单位向量 JSON
    raw = (np.ones(EMBEDDING_DIM, dtype=np.float32) * 2.0).tolist()
    import json

    enc.decrypt = MagicMock(return_value=json.dumps(raw).encode())
    p = MagicMock(feature_data=b"x", id=1)
    arr = FaceEmbeddingCache._decrypt_vec(enc, p)
    assert arr is not None
    assert abs(float(np.linalg.norm(arr)) - 1.0) < 1e-5
