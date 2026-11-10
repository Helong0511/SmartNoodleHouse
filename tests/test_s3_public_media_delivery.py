from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.media import get_public_media
from app.api.products import _assert_safe_product_image_url
from app.core.exceptions import AppException
from app.models import Store
from app.services import object_storage


def test_product_image_url_accepts_only_supported_storage_urls(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(s3_public_base_url="https://cdn.example.test/assets"),
    )
    key = "products/0123456789abcdef0123456789abcdef.webp"
    assert _assert_safe_product_image_url(f"/api/media/public/{key}") == f"/api/media/public/{key}"
    assert _assert_safe_product_image_url(f"https://cdn.example.test/assets/{key}") == (
        f"https://cdn.example.test/assets/{key}"
    )
    assert _assert_safe_product_image_url("/uploads/products/legacy.webp") == (
        "/uploads/products/legacy.webp"
    )
    for unsafe in (
        "https://attacker.example/products/0123456789abcdef0123456789abcdef.webp",
        "/api/media/public/../private/secret.webp",
        "/api/media/public/avatars/0123456789abcdef0123456789abcdef.webp",
        "s3://private-bucket/products/0123456789abcdef0123456789abcdef.webp",
    ):
        with pytest.raises(AppException):
            _assert_safe_product_image_url(unsafe)


@pytest.mark.asyncio
async def test_s3_storage_returns_frontend_renderable_url_without_public_cdn():
    class FakeS3Client:
        def put_object(self, **kwargs):
            self.last_put = kwargs

    storage = object.__new__(object_storage.S3ObjectStorage)
    storage.client = FakeS3Client()
    storage.bucket = "private-bucket"
    storage.public_base = ""
    storage.endpoint = ""
    key = "products/0123456789abcdef0123456789abcdef.webp"

    url = await storage.put_bytes(key, b"image", "image/webp")

    assert url == f"/api/media/public/{key}"
    assert storage.client.last_put["Bucket"] == "private-bucket"
    assert storage.client.last_put["Key"] == key

    # 头像等非公开前缀不能被映射到匿名媒体路由或公开 CDN。
    private_url = await storage.put_bytes(
        "avatars/abcdef0123456789abcdef0123456789.webp",
        b"private",
        "image/webp",
    )
    assert private_url == "s3://private-bucket/avatars/abcdef0123456789abcdef0123456789.webp"

    storage.public_base = "https://cdn.example.test/assets"
    cdn_url = await storage.put_bytes(key, b"image", "image/webp")
    assert cdn_url == f"https://cdn.example.test/assets/{key}"


@pytest.mark.asyncio
async def test_public_media_is_served_only_while_referenced_by_store(db_session, monkeypatch):
    key = "stores/0123456789abcdef0123456789abcdef.webp"
    public_path = f"/api/media/public/{key}"
    store = Store(store_no="MEDIA-TEST-01", name="媒体测试门店", logo=public_path, status=1)
    db_session.add(store)
    await db_session.flush()

    class FakeStorage:
        async def get_bytes(self, requested_key):
            assert requested_key == key
            return b"fake-webp"

    monkeypatch.setattr("app.api.media.get_storage", lambda: FakeStorage())
    response = await get_public_media(key, db_session)
    assert response.body == b"fake-webp"
    assert response.media_type == "image/webp"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"].startswith("public, max-age=3600")

    store.logo = None
    await db_session.flush()
    with pytest.raises(AppException) as exc:
        await get_public_media(key, db_session)
    assert exc.value.status_code == 404
