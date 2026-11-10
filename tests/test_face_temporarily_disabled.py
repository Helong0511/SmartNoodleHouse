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
