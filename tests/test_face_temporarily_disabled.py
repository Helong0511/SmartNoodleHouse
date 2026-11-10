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
