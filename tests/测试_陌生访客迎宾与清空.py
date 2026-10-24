"""陌生访客：访客号复用、迎宾播报开关、清空事件。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.face_service import FaceService, VisitorService
from app.services.system_settings_service import BUSINESS_SETTING_META, SystemSettingsService


def test_unknown_visitor_welcome_meta_store_override():
    assert SystemSettingsService.allows_store_override("unknown_visitor_welcome_enabled") is True
    assert SystemSettingsService.allows_store_override("unknown_visitor_welcome_text") is True
    assert SystemSettingsService.allows_store_override("unknown_visitor_welcome_cooldown_seconds") is True
    meta = BUSINESS_SETTING_META["unknown_visitor_welcome_text"]
    assert meta["group"] == "welcome"
    assert meta.get("default") == "欢迎光临"
    assert SystemSettingsService._validate_value("unknown_visitor_welcome_enabled", "0") == "false"
    assert SystemSettingsService._validate_value("unknown_visitor_welcome_text", "欢迎光临本店") == "欢迎光临本店"


def test_resolve_visitor_no_reuses_similar_embedding():
    VisitorService._unknown_tracks.clear()
    cam_id = 88001
    emb1 = np.zeros(512, dtype=np.float32)
    emb1[0] = 1.0
    no1 = VisitorService.resolve_visitor_no(cam_id, store_no="SH01", embedding=emb1, reuse_min_sim=0.4)
    emb2 = emb1.copy()
    emb2[1] = 0.01
    emb2 = emb2 / np.linalg.norm(emb2)
    emb1n = emb1 / np.linalg.norm(emb1)
    # 几乎同向 → 应复用
    sim = FaceService.cosine_similarity(emb1n, emb2)
    assert sim >= 0.4
    no2 = VisitorService.resolve_visitor_no(cam_id, store_no="SH01", embedding=emb2, reuse_min_sim=0.4)
    assert no2 == no1
    # 正交向量 → 新号
    emb3 = np.zeros(512, dtype=np.float32)
    emb3[10] = 1.0
    no3 = VisitorService.resolve_visitor_no(cam_id, store_no="SH01", embedding=emb3, reuse_min_sim=0.4)
    assert no3 != no1
    VisitorService._unknown_tracks.clear()


@pytest.mark.asyncio
async def test_maybe_welcome_unknown_respects_store_switch_and_cooldown():
    VisitorService._last_unknown_welcome_at.clear()
    db = MagicMock()
    q: list = []

    async def _bool(db, key, fallback, store_id=None):
        if key == "unknown_visitor_welcome_enabled":
            return True
        return fallback

    async def _int(db, key, fallback, store_id=None):
        if key == "unknown_visitor_welcome_cooldown_seconds":
            return 60
        return fallback

    async def _setting(db, key, store_id=None, fallback=None):
        if key == "unknown_visitor_welcome_text":
            return "欢迎光临测试店"
        return fallback

    from app.services.face_service import WelcomeService

    async def _enqueue(item):
        q.append(item)

    with (
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_bool",
            new=AsyncMock(side_effect=_bool),
        ),
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_int",
            new=AsyncMock(side_effect=_int),
        ),
        patch(
            "app.services.system_settings_service.SystemSettingsService.effective_setting",
            new=AsyncMock(side_effect=_setting),
        ),
        patch.object(WelcomeService, "enqueue", new=AsyncMock(side_effect=_enqueue)),
        patch("app.services.websocket_manager.ws_manager.broadcast", new=AsyncMock()),
    ):
        ok1 = await VisitorService.maybe_welcome_unknown(db, 8, store_id=1)
        ok2 = await VisitorService.maybe_welcome_unknown(db, 8, store_id=1)
    assert ok1 is True
    assert ok2 is False  # 冷却中
    assert len(q) == 1
    assert q[0]["text"] == "欢迎光临测试店"
    assert q[0]["camera_id"] == 8
    VisitorService._last_unknown_welcome_at.clear()


@pytest.mark.asyncio
async def test_maybe_welcome_unknown_disabled():
    VisitorService._last_unknown_welcome_at.clear()
    db = MagicMock()

    async def _bool(db, key, fallback, store_id=None):
        return False

    with patch(
        "app.services.system_settings_service.SystemSettingsService.effective_bool",
        new=AsyncMock(side_effect=_bool),
    ):
        ok = await VisitorService.maybe_welcome_unknown(db, 8, store_id=1)
    assert ok is False
    VisitorService._last_unknown_welcome_at.clear()


@pytest.mark.asyncio
async def test_clear_events_deletes_rows_and_files():
    from app.models import VisitorEvent

    db = MagicMock()
    row = MagicMock(spec=VisitorEvent)
    row.snapshot_path = "/uploads/visitors/2026/10/24/cam8_a.jpg"
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [row]
    delete_result = MagicMock()
    delete_result.rowcount = 1

    async def _execute(stmt):
        sql = str(stmt)
        if "DELETE" in sql.upper() or getattr(stmt, "__class__", None).__name__ == "Delete":
            return delete_result
        return exec_result

    db.execute = AsyncMock(side_effect=_execute)
    storage = MagicMock()
    storage.delete = AsyncMock(return_value=True)

    with (
        patch("app.services.object_storage.get_storage", return_value=storage),
        patch.object(VisitorService, "_purge_local_camera_snapshots", AsyncMock(return_value=0)),
    ):
        stats = await VisitorService.clear_events(db, camera_ids=[8])
    assert stats["deleted_rows"] == 1
    assert stats["deleted_files"] == 1
    assert stats.get("orphan_files", 0) == 0
    storage.delete.assert_awaited()
