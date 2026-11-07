from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_disabled_face_welcome_stops_worker_before_model_or_camera_processing(monkeypatch):
    """The global face feature flag must gate all biometric frame processing."""
    from app.core import config as config_module
    from app.core import database as database_module
    from app.services.face_service import face_embedding_cache
    from app.services.system_settings_service import SystemSettingsService
    from app.workers import recognition_worker as worker_module
    import camera.camera_manager as camera_manager_module

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    settings = SimpleNamespace(
        enable_face_welcome=True,
        is_production=True,
        face_recognition_legal_reviewed=False,
        # Even if the admin toggle and legacy capture switch are true, the
        # production legal-review gate must prevent biometric processing.
        unknown_visitor_capture_enabled=True,
    )
    monkeypatch.setattr(database_module, "AsyncSessionLocal", FakeSession)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        SystemSettingsService,
        "effective_bool",
        AsyncMock(return_value=True),
    )

    invalidate = MagicMock()
    get_profiles = AsyncMock(side_effect=AssertionError("must not load face profiles"))
    monkeypatch.setattr(face_embedding_cache, "invalidate", invalidate)
    monkeypatch.setattr(face_embedding_cache, "get_profiles", get_profiles)

    model_loader = MagicMock(side_effect=AssertionError("must not load face model"))
    frame_extractor = MagicMock(side_effect=AssertionError("must not inspect camera frames"))
    camera_manager = MagicMock(side_effect=AssertionError("must not enumerate cameras"))
    monkeypatch.setattr(
        "app.services.face_extractor.ensure_live_face_app",
        model_loader,
    )
    monkeypatch.setattr(
        "app.services.face_extractor.extract_live_face_hit",
        frame_extractor,
    )
    monkeypatch.setattr(
        camera_manager_module,
        "CameraManager",
        camera_manager,
    )

    worker = worker_module.RecognitionWorker()

    async def stop_after_disabled_wait(_delay):
        worker.stop()

    monkeypatch.setattr(worker_module.asyncio, "sleep", stop_after_disabled_wait)
    await worker.run()

    assert invalidate.call_count == 1
    SystemSettingsService.effective_bool.assert_not_awaited()
    get_profiles.assert_not_awaited()
    model_loader.assert_not_called()
    frame_extractor.assert_not_called()
    camera_manager.assert_not_called()


def test_unknown_visitor_face_processing_defaults_are_off():
    from app.core.config import Settings
    from app.services.system_settings_service import BUSINESS_SETTING_META

    assert Settings.model_fields["enable_face_welcome"].default is False
    assert Settings.model_fields["face_recognition_legal_reviewed"].default is False
    assert Settings.model_fields["unknown_visitor_capture_enabled"].default is False
    assert Settings.model_fields["unknown_visitor_welcome_enabled"].default is False
    assert BUSINESS_SETTING_META["unknown_visitor_capture_enabled"]["default"] is False
    assert BUSINESS_SETTING_META["unknown_visitor_welcome_enabled"]["default"] is False
