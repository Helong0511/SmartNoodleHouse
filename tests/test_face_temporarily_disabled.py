"""Temporary face-recognition shutdown is a hard gate, not a UI preference."""
import asyncio

import pytest


def test_face_recognition_is_temporarily_disabled():
    from app.services.face_feature_gate import FACE_RECOGNITION_TEMPORARILY_DISABLED

    assert FACE_RECOGNITION_TEMPORARILY_DISABLED is True


def test_miniprogram_face_enrollment_and_challenge_are_blocked():
    from app.api.miniprogram_face import _require_face_welcome_enabled
    from app.core.exceptions import AppException

    with pytest.raises(AppException) as exc:
        asyncio.run(_require_face_welcome_enabled(None))

    assert exc.value.code == 40344



def test_direct_model_entrypoints_are_blocked_before_loading():
    import numpy as np

    from app.services.face_extractor import (
        ensure_live_face_app,
        extract_face_embedding_bgr,
        extract_live_face_hit,
    )

    with pytest.raises(RuntimeError, match="人脸识别功能临时关闭"):
        ensure_live_face_app()
    from app.services.face_extractor import extract_face_embedding

    # The hard stop must run before OpenCV decodes even transient image bytes.
    with pytest.raises(RuntimeError, match="人脸识别功能临时关闭"):
        extract_face_embedding(b"not-an-image")

    from app.services.face_extractor import extract_face_embedding_with_quality

    with pytest.raises(RuntimeError, match="人脸识别功能临时关闭"):
        extract_face_embedding_with_quality(b"not-an-image")

    with pytest.raises(RuntimeError, match="人脸识别功能临时关闭"):
        extract_face_embedding_bgr(np.zeros((160, 160, 3), dtype=np.uint8))
    with pytest.raises(RuntimeError, match="人脸识别功能临时关闭"):
        extract_live_face_hit(np.zeros((160, 160, 3), dtype=np.uint8))


def test_startup_gate_blocks_face_profile_warmup_in_all_environments():
    from types import SimpleNamespace

    from app.services.face_feature_gate import is_face_recognition_allowed

    development = SimpleNamespace(
        is_production=False,
        face_recognition_legal_reviewed=False,
        face_model_commercial_license_confirmed=False,
    )
    production_approved = SimpleNamespace(
        is_production=True,
        face_recognition_legal_reviewed=True,
        face_model_commercial_license_confirmed=True,
    )

    assert is_face_recognition_allowed(development) is False
    assert is_face_recognition_allowed(production_approved) is False



def test_face_service_layer_blocks_direct_profile_reads_and_writes():
    import numpy as np

    from app.services.face_service import FaceEmbeddingCache, FaceService

    cache = FaceEmbeddingCache()
    blocked = "人脸识别功能临时关闭"

    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(cache.warmup(None))
    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(cache.get_profiles(None))
    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(cache.promote(None, 123))
    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(cache.best_match(None, np.zeros(512, dtype=np.float32), 0.5))
    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(FaceService.register_face(None, 123, [0.0] * 512))
    with pytest.raises(RuntimeError, match=blocked):
        asyncio.run(FaceService.reencrypt_stale_profiles(None))
