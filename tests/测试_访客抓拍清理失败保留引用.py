"""访客抓拍清理：对象删除失败时必须保留 DB 引用，供后续重试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.face_service import VisitorService


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_clear_events_keeps_db_row_when_snapshot_delete_fails():
    rows = [
        SimpleNamespace(id=11, camera_id=7, snapshot_path="private://visitors/good.jpg"),
        SimpleNamespace(id=12, camera_id=7, snapshot_path="private://visitors/retry.jpg"),
    ]
    db = MagicMock()
    delete_result = SimpleNamespace(rowcount=1)
    db.execute = AsyncMock(side_effect=[_Rows(rows), delete_result])
    redis = SimpleNamespace(delete=AsyncMock())
    storage = SimpleNamespace(
        delete=AsyncMock(side_effect=[True, OSError("storage temporarily unavailable")])
    )

    with patch("app.services.object_storage.get_storage", return_value=storage), patch(
        "app.services.face_service.get_redis", new=AsyncMock(return_value=redis)
    ), patch.object(
        VisitorService, "_purge_local_camera_snapshots", new=AsyncMock(return_value=0)
    ):
        result = await VisitorService.clear_events(db, camera_ids=[7])

    assert result["deleted_rows"] == 1
    assert result["deleted_files"] == 1
    delete_stmt = db.execute.await_args_list[1].args[0]
    compiled = delete_stmt.compile()
    assert "visitor_events.id IN" in str(delete_stmt)
    bound_values = [
        item
        for value in compiled.params.values()
        for item in (value if isinstance(value, (list, tuple, set)) else [value])
    ]
    assert 11 in bound_values
    assert 12 not in bound_values


@pytest.mark.asyncio
async def test_cleanup_expired_keeps_db_row_when_snapshot_delete_fails(monkeypatch):
    rows = [
        SimpleNamespace(id=21, expires_at=None, snapshot_path="private://visitors/good.jpg"),
        SimpleNamespace(id=22, expires_at=None, snapshot_path="private://visitors/retry.jpg"),
    ]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_Rows(rows), SimpleNamespace(rowcount=1)])
    db.commit = AsyncMock()
    storage = SimpleNamespace(
        delete=AsyncMock(side_effect=[True, OSError("storage temporarily unavailable")])
    )

    with patch("app.services.object_storage.get_storage", return_value=storage), patch(
        "app.services.system_settings_service.SystemSettingsService.get_int",
        new=AsyncMock(return_value=30),
    ), patch.object(
        VisitorService, "_prune_local_visitor_dirs", new=AsyncMock(return_value=0)
    ):
        result = await VisitorService.cleanup_expired(db)

    assert result["deleted_rows"] == 1
    assert result["deleted_files"] == 1
    assert db.commit.await_count == 1
    delete_stmt = db.execute.await_args_list[1].args[0]
    compiled = delete_stmt.compile()
    assert "visitor_events.id IN" in str(delete_stmt)
    bound_values = [
        item
        for value in compiled.params.values()
        for item in (value if isinstance(value, (list, tuple, set)) else [value])
    ]
    assert 21 in bound_values
    assert 22 not in bound_values
