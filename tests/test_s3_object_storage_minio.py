from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from app.services.object_storage import S3ObjectStorage


@pytest.mark.asyncio
async def test_s3_object_storage_against_real_minio(monkeypatch):
    """Exercise the real boto3 adapter against a disposable S3-compatible server."""
    endpoint = (os.getenv("S3_TEST_ENDPOINT") or "").strip()
    if not endpoint:
        pytest.skip("S3_TEST_ENDPOINT is not configured; MinIO integration runs in CI")

    bucket = f"snh-ci-{uuid.uuid4().hex[:20]}"
    settings = SimpleNamespace(
        s3_bucket=bucket,
        s3_access_key=os.getenv("S3_TEST_ACCESS_KEY", "minioadmin"),
        s3_secret_key=os.getenv("S3_TEST_SECRET_KEY", "minioadmin"),
        s3_endpoint=endpoint,
        s3_region="us-east-1",
        s3_public_base_url="",
    )
    monkeypatch.setattr("app.services.object_storage.get_settings", lambda: settings)
    storage = S3ObjectStorage()
    storage.client.create_bucket(Bucket=bucket)

    public_key = f"products/{uuid.uuid4().hex}.webp"
    private_key = f"avatars/{uuid.uuid4().hex}.webp"
    payload = b"smart-noodle-minio-integration\x00payload"
    try:
        public_url = await storage.put_bytes(public_key, payload, "image/webp")
        assert public_url == f"/api/media/public/{public_key}"
        assert await storage.get_bytes(public_key) == payload

        # Private objects must never be mapped to the anonymous public-media route.
        private_url = await storage.put_bytes(private_key, payload, "image/webp")
        assert not private_url.startswith("/api/media/public/")
        assert private_url.startswith(("s3://", "/api/media/private/"))
        assert await storage.get_bytes(private_key) == payload

        assert await storage.delete(public_key) is True
        assert await storage.delete(private_key) is True
        # S3 DeleteObject is intentionally idempotent for an already absent key.
        assert await storage.delete(public_key) is True
        missing_key = f"products/{uuid.uuid4().hex}.webp"
        assert await storage.delete(missing_key) is True
        with pytest.raises(FileNotFoundError):
            await storage.get_bytes(public_key)

        with pytest.raises(FileNotFoundError):
            await storage.get_bytes(missing_key)
    finally:
        # MinIO DeleteObject is idempotent; clean up even when an assertion fails.
        await storage.delete(public_key)
        await storage.delete(private_key)
        storage.client.delete_bucket(Bucket=bucket)
