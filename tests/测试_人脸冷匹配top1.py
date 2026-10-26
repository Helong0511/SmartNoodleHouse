# -*- coding: utf-8 -*-
"""冷库匹配必须在预算内取最高相似度，禁止首个达标即返回。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.face_extractor import EMBEDDING_DIM
from app.services.face_service import FaceEmbeddingCache, FaceService


def _vec_with_cosine(query: np.ndarray, cosine: float, *, seed: int) -> np.ndarray:
    """构造与 unit query 近似给定余弦相似度的单位向量。"""
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    q = q / (np.linalg.norm(q) + 1e-8)
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(q.size).astype(np.float32)
    n = n - float(np.dot(n, q)) * q
    n = n / (np.linalg.norm(n) + 1e-8)
    c = float(np.clip(cosine, -1.0, 1.0))
    v = c * q + np.sqrt(max(0.0, 1.0 - c * c)) * n
    return v / (np.linalg.norm(v) + 1e-8)


def _fake_query():
    fake_q = MagicMock()
    fake_q.where.return_value = fake_q
    fake_q.order_by.return_value = fake_q
    fake_q.offset.return_value = fake_q
    fake_q.limit.return_value = fake_q
    return fake_q


@pytest.mark.asyncio
async def test_cold_match_picks_highest_sim_not_first_above_threshold(monkeypatch):
    """A≈0.47 排在 B≈0.78 前面且阈值=0.45 时，必须选 B。"""
    cache = FaceEmbeddingCache()
    query = np.ones(EMBEDDING_DIM, dtype=np.float32)
    query = query / (np.linalg.norm(query) + 1e-8)

    a_vec = _vec_with_cosine(query, 0.47, seed=1)
    b_vec = _vec_with_cosine(query, 0.78, seed=2)
    sim_a = FaceService.cosine_similarity(query, a_vec)
    sim_b = FaceService.cosine_similarity(query, b_vec)
    assert abs(sim_a - 0.47) < 1e-5
    assert abs(sim_b - 0.78) < 1e-5
    assert sim_a >= 0.45

    rows = [
        SimpleNamespace(customer_id=101, id=1, feature_data=b"a"),
        SimpleNamespace(customer_id=202, id=2, feature_data=b"b"),
    ]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return rows

    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result())

    def _decrypt(_enc, p):
        if int(p.customer_id) == 101:
            return a_vec
        if int(p.customer_id) == 202:
            return b_vec
        return None

    monkeypatch.setattr(FaceEmbeddingCache, "_decrypt_vec", staticmethod(_decrypt))
    monkeypatch.setattr(FaceEmbeddingCache, "_authorized_base", lambda self: _fake_query())
    monkeypatch.setattr(
        FaceEmbeddingCache, "_activity_score_expr", lambda self: MagicMock()
    )

    best_id, best_sim = await cache._cold_match(db, query, threshold=0.45, hot_ids=set())
    assert best_id == 202
    assert abs(best_sim - sim_b) < 1e-5
    assert best_sim > sim_a


@pytest.mark.asyncio
async def test_cold_match_no_candidate_returns_none(monkeypatch):
    cache = FaceEmbeddingCache()
    query = np.ones(EMBEDDING_DIM, dtype=np.float32)
    query = query / (np.linalg.norm(query) + 1e-8)

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    db = MagicMock()
    db.execute = AsyncMock(return_value=_Result())
    monkeypatch.setattr(FaceEmbeddingCache, "_authorized_base", lambda self: _fake_query())
    monkeypatch.setattr(
        FaceEmbeddingCache, "_activity_score_expr", lambda self: MagicMock()
    )
    monkeypatch.setattr(
        FaceEmbeddingCache, "_decrypt_vec", staticmethod(lambda *_a, **_k: None)
    )
    best_id, best_sim = await cache._cold_match(db, query, threshold=0.45, hot_ids=set())
    assert best_id is None
    assert best_sim == 0.0
