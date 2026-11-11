"""Production guard for InsightFace pretrained-model commercial licensing."""
from types import SimpleNamespace

import pytest


def _settings(*, legal_reviewed: bool, license_confirmed: bool):
    return SimpleNamespace(
        is_production=True,
        face_recognition_legal_reviewed=legal_reviewed,
        face_model_commercial_license_confirmed=license_confirmed,
    )


def test_production_face_model_load_requires_commercial_license(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(legal_reviewed=True, license_confirmed=False),
    )
    with pytest.raises(RuntimeError, match="未确认商业授权"):
        face_extractor._assert_face_model_commercial_license()


def test_production_face_model_load_requires_legal_review(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(legal_reviewed=False, license_confirmed=True),
    )
    with pytest.raises(RuntimeError, match="FACE_RECOGNITION_LEGAL_REVIEWED"):
        face_extractor._assert_face_model_commercial_license()


def test_production_face_model_load_accepts_confirmed_license(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(legal_reviewed=True, license_confirmed=True),
    )
    face_extractor._assert_face_model_commercial_license()


def test_development_face_model_load_does_not_require_commercial_ack(monkeypatch):
    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            is_production=False,
            face_recognition_legal_reviewed=False,
            face_model_commercial_license_confirmed=False,
        ),
    )
    face_extractor._assert_face_model_commercial_license()


@pytest.mark.parametrize(
    "entrypoint",
    [
        "extract_face_embedding_bgr",
        "extract_live_face_hit",
        "_face_app",
        "_face_app_live",
    ],
)
def test_production_inference_entrypoints_fail_closed_before_model_use(
    monkeypatch, entrypoint
):
    """Every production inference entrypoint must reject missing license before model work."""
    import numpy as np

    from app.services import face_extractor

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(legal_reviewed=True, license_confirmed=False),
    )
    monkeypatch.setattr(
        face_extractor,
        "_insightface_available",
        lambda: pytest.fail("model availability must not be inspected before licensing"),
    )
    target = getattr(face_extractor, entrypoint)
    if entrypoint in {"extract_face_embedding_bgr", "extract_live_face_hit"}:
        with pytest.raises(RuntimeError, match="临时关闭"):
            target(np.zeros((32, 32, 3), dtype=np.uint8))
    else:
        target.cache_clear()
        try:
            with pytest.raises(RuntimeError, match="临时关闭"):
                target()
        finally:
            target.cache_clear()



def test_production_template_keeps_unlicensed_face_welcome_disabled():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = (root / ".env.production.example").read_text(encoding="utf-8")
    deployment = (root / "docs/部署说明.md").read_text(encoding="utf-8")

    values = {}
    for line in env.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")

    assert values["ENABLE_FACE_WELCOME"].lower() == "false"
    assert values["FACE_RECOGNITION_LEGAL_REVIEWED"].lower() == "false"
    assert values["FACE_MODEL_COMMERCIAL_LICENSE_CONFIRMED"].lower() == "false"
    assert "不得为了绕过启动门禁而直接把确认变量改为" in deployment
    assert "非商业研究" in deployment
