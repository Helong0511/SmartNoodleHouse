"""Production guard for InsightFace pretrained-model commercial licensing."""
from types import SimpleNamespace

import pytest


def test_production_face_model_load_requires_commercial_license(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(is_production=True, face_recognition_legal_reviewed=True, face_model_commercial_license_confirmed=False),
    )
    with pytest.raises(RuntimeError, match="未确认商业授权"):
        face_extractor._assert_face_model_commercial_license()


def test_production_face_model_load_requires_legal_review(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(is_production=True, face_recognition_legal_reviewed=False, face_model_commercial_license_confirmed=True),
    )
    with pytest.raises(RuntimeError, match="FACE_RECOGNITION_LEGAL_REVIEWED"):
        face_extractor._assert_face_model_commercial_license()


def test_production_face_model_load_accepts_confirmed_license(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(is_production=True, face_recognition_legal_reviewed=True, face_model_commercial_license_confirmed=True),
    )
    face_extractor._assert_face_model_commercial_license()


def test_development_face_model_load_does_not_require_commercial_ack(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(is_production=False, face_model_commercial_license_confirmed=False),
    )
    face_extractor._assert_face_model_commercial_license()
