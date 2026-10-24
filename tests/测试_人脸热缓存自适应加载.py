"""人脸热集定容 / Top-N / promote / touch / 冷命中路径。"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.core.warmup_budget import (
    face_cache_max_profiles,
    is_low_memory,
)
from app.services.face_extractor import EMBEDDING_DIM
from app.services.face_service import FaceEmbeddingCache, FaceService


def _vec(seed: float = 0.1) -> np.ndarray:
    rng = np.random.default_rng(int(seed * 1000) % 2**31)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    v /= np.linalg.norm(v) + 1e-9
    return v


def test_face_cache_max_profiles_tiers():
    assert face_cache_max_profiles(override=0, available_bytes=1 * 1024**3) == 200
    assert face_cache_max_profiles(override=0, available_bytes=2 * 1024**3) == 800
    assert face_cache_max_profiles(override=0, available_bytes=4 * 1024**3) == 2000
    assert face_cache_max_profiles(override=0, available_bytes=8 * 1024**3) == 5000
    assert face_cache_max_profiles(override=123, available_bytes=1) == 123
    assert face_cache_max_profiles(override=99999, available_bytes=1) == 5000


def test_is_low_memory():
    assert is_low_memory(available_bytes=1024**3) is True
    assert is_low_memory(available_bytes=2 * 1024**3) is False


@pytest.mark.asyncio
async def test_evict_overflow_keeps_touched():
    cache = FaceEmbeddingCache()
    cache._max_profiles = 2
    with patch.object(cache, "_resolve_max", return_value=2):
        v1, v2, v3 = _vec(1), _vec(2), _vec(3)
        cache._by_customer = {
            1: (10, v1),
            2: (20, v2),
            3: (30, v3),
        }
        cache._touch_at = {1: 100.0, 2: 50.0, 3: 10.0}
        cache._evict_overflow()
        assert 3 not in cache._by_customer
        assert set(cache._by_customer.keys()) == {1, 2}


@pytest.mark.asyncio
async def test_best_match_hot_hit_touches():
    cache = FaceEmbeddingCache()
    emb = _vec(7)
    cache._by_customer = {42: (1, emb.copy())}
    cache._touch_at = {42: 0.0}
    db = AsyncMock()
    with patch.object(cache, "_refresh", new_callable=AsyncMock):
        cid, sim = await cache.best_match(db, emb, threshold=0.4, camera_id=1)
    assert cid == 42
    assert sim >= 0.99
    assert cache._touch_at[42] > 0.0


@pytest.mark.asyncio
async def test_best_match_cold_hit_promotes():
    cache = FaceEmbeddingCache()
    cache._max_profiles = 8
    hot = _vec(1)
    cold = _vec(2)
    cache._by_customer = {1: (11, hot)}
    db = AsyncMock()

    cold_profile = SimpleNamespace(
        id=99,
        customer_id=99,
        feature_data=b"x",
        consent_status="AUTHORIZED",
        updated_at=None,
        consent_at=None,
    )

    async def fake_cold(db_, emb, threshold, hot_ids):
        return 99, 0.95

    with (
        patch.object(cache, "_refresh", new_callable=AsyncMock),
        patch.object(cache, "_cold_match", side_effect=fake_cold),
        patch.object(cache, "_resolve_max", return_value=8),
        patch.object(
            cache,
            "_decrypt_vec",
            return_value=cold,
        ),
    ):
        # authorize base query for promote-in-lock path
        result = MagicMock()
        result.scalar_one_or_none.return_value = cold_profile
        db.execute = AsyncMock(return_value=result)
        cid, sim = await cache.best_match(db, cold, threshold=0.48, camera_id=5)
    assert cid == 99
    assert sim >= 0.9
    assert 99 in cache._by_customer


@pytest.mark.asyncio
async def test_promote_noop_without_profile():
    cache = FaceEmbeddingCache()
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    ok = await cache.promote(db, 12345)
    assert ok is False


def test_cosine_self():
    v = _vec(3)
    assert FaceService.cosine_similarity(v, v) > 0.99
