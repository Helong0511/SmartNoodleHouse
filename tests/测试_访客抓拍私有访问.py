from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import pytest

from app.api.cameras import get_visitor_snapshot
from app.core.response import now_local
from app.services.face_service import VisitorService
from app.services.object_storage import LocalObjectStorage
from app.services.visitor_snapshot_access import (
    build_visitor_snapshot_url,
    verify_visitor_snapshot_signature,
)


def test_visitor_snapshot_key_parser_accepts_private_and_legacy_paths():
    key = "visitors/2026/11/04/cam1_test.jpg"
    assert VisitorService.uploads_url_to_key(f"private://{key}") == key
    assert VisitorService.uploads_url_to_key(f"/uploads/{key}") == key
    assert VisitorService.uploads_url_to_key(f"https://cdn.example.com/{key}") == key
    assert VisitorService.uploads_url_to_key(f"s3://private-bucket/{key}") == key
    assert VisitorService.uploads_url_to_key("private://visitors/../secret.txt") is None
    assert VisitorService.uploads_url_to_key("https://evil.example/other/file.jpg") is None


def test_visitor_snapshot_url_signature_expires_and_rejects_tampering(monkeypatch):
    monkeypatch.setattr(
        "app.services.visitor_snapshot_access.get_settings",
        lambda: SimpleNamespace(
            secret_encryption_key="current-secret-key-material-0123456789",
            secret_encryption_key_previous="",
            face_encryption_key="face-key-material-0123456789",
            jwt_secret="jwt-secret-material-0123456789",
        ),
    )
    key = "visitors/2026/11/04/cam1_test.jpg"
    url = build_visitor_snapshot_url(12, 3, key, now=1_000)
    query = parse_qs(urlsplit(url).query)
    exp = int(query["exp"][0])
    sig = query["sig"][0]

    assert exp == 1_300
    assert verify_visitor_snapshot_signature(12, 3, key, exp, sig, now=1_200) is True
    assert verify_visitor_snapshot_signature(12, 3, key, exp, sig, now=1_301) is False
    assert verify_visitor_snapshot_signature(12, 4, key, exp, sig, now=1_200) is False
    assert verify_visitor_snapshot_signature(12, 3, key, exp, "0" * 64, now=1_200) is False


def test_visitor_snapshot_signature_supports_previous_key_during_rotation(monkeypatch):
    old_key = "previous-secret-key-material-0123456789"
    monkeypatch.setattr(
        "app.services.visitor_snapshot_access.get_settings",
        lambda: SimpleNamespace(
            secret_encryption_key=old_key,
            secret_encryption_key_previous="",
            face_encryption_key="face-key-material-0123456789",
            jwt_secret="jwt-secret-material-0123456789",
        ),
    )
    url = build_visitor_snapshot_url(12, 3, "visitors/old.jpg", now=1_000)
    query = parse_qs(urlsplit(url).query)

    monkeypatch.setattr(
        "app.services.visitor_snapshot_access.get_settings",
        lambda: SimpleNamespace(
            secret_encryption_key="new-secret-key-material-0123456789",
            secret_encryption_key_previous=old_key,
            face_encryption_key="face-key-material-0123456789",
            jwt_secret="jwt-secret-material-0123456789",
        ),
    )
    assert verify_visitor_snapshot_signature(
        12, 3, "visitors/old.jpg", int(query["exp"][0]), query["sig"][0], now=1_200
    ) is True


@pytest.mark.asyncio
async def test_local_object_storage_can_read_private_visitor_object(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.object_storage.get_settings",
        lambda: SimpleNamespace(upload_dir=str(tmp_path)),
    )
    storage = LocalObjectStorage()
    await storage.put_bytes("visitors/test.jpg", b"private-image", "image/jpeg")

    assert await storage.get_bytes("visitors/test.jpg") == b"private-image"
    with pytest.raises(ValueError):
        await storage.get_bytes("../outside.txt")


@pytest.mark.asyncio
async def test_save_frame_snapshot_stores_private_reference(monkeypatch):
    storage = SimpleNamespace(put_bytes=AsyncMock(return_value="/uploads/visitors/test.jpg"))
    monkeypatch.setattr("app.services.object_storage.get_storage", lambda: storage)
    monkeypatch.setattr(VisitorService, "encode_frame_jpeg", staticmethod(lambda frame: b"jpeg"))
    monkeypatch.setattr(VisitorService, "make_snapshot_key", staticmethod(lambda camera_id, ext="jpg": "visitors/test.jpg"))

    result = await VisitorService.save_frame_snapshot(None, 1)

    assert result == "private://visitors/test.jpg"
    storage.put_bytes.assert_awaited_once_with("visitors/test.jpg", b"jpeg", content_type="image/jpeg")



class _SnapshotDB:
    def __init__(self, event, camera):
        self.event = event
        self.camera = camera
        self.calls = 0

    async def get(self, model, row_id):
        self.calls += 1
        return self.event if self.calls == 1 else self.camera


@pytest.mark.asyncio
async def test_visitor_snapshot_route_streams_only_valid_signed_private_object(monkeypatch):
    from datetime import timedelta

    event = SimpleNamespace(
        id=12,
        camera_id=3,
        snapshot_path="private://visitors/test.jpg",
        expires_at=now_local() + timedelta(days=1),
    )
    camera = SimpleNamespace(id=3, store_id=7)
    db = _SnapshotDB(event, camera)
    image_bytes = b"\xff\xd8\xffprivate-jpeg"
    storage = SimpleNamespace(get_bytes=AsyncMock(return_value=image_bytes))
    monkeypatch.setattr(
        "app.services.visitor_snapshot_access.verify_visitor_snapshot_signature",
        lambda *args: True,
    )
    monkeypatch.setattr("app.services.object_storage.get_storage", lambda: storage)

    response = await get_visitor_snapshot(12, 1_900_000_000, "a" * 64, db)

    assert response.body == image_bytes
    assert response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    storage.get_bytes.assert_awaited_once_with("visitors/test.jpg")


@pytest.mark.asyncio
async def test_visitor_snapshot_route_rejects_invalid_signature_before_storage(monkeypatch):
    from datetime import timedelta

    event = SimpleNamespace(
        id=12,
        camera_id=3,
        snapshot_path="private://visitors/test.jpg",
        expires_at=now_local() + timedelta(days=1),
    )
    camera = SimpleNamespace(id=3, store_id=7)
    db = _SnapshotDB(event, camera)
    storage = SimpleNamespace(get_bytes=AsyncMock(return_value=b""))
    monkeypatch.setattr(
        "app.services.visitor_snapshot_access.verify_visitor_snapshot_signature",
        lambda *args: False,
    )
    monkeypatch.setattr("app.services.object_storage.get_storage", lambda: storage)

    from app.core.exceptions import AppException

    with pytest.raises(AppException, match="访问链接已过期"):
        await get_visitor_snapshot(12, 1_900_000_000, "0" * 64, db)
    storage.get_bytes.assert_not_awaited()
