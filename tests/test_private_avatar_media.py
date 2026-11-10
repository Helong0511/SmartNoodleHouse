from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.media import get_private_avatar
from app.core.exceptions import AppException
from app.models import Customer
from app.services.object_storage import LocalObjectStorage
from app.services.private_media import (
    avatar_object_key,
    sign_private_avatar_url,
    verify_private_avatar_signature,
)


_SECRET = "test-private-media-signing-secret-32-bytes-minimum"


def _settings():
    return SimpleNamespace(jwt_secret=_SECRET, s3_bucket="private-bucket")


def test_private_avatar_urls_are_short_lived_and_bound_to_customer(monkeypatch):
    monkeypatch.setattr("app.services.private_media.get_settings", _settings)
    stored = "/api/media/private/avatars/abcdef0123456789abcdef0123456789.webp"
    signed = sign_private_avatar_url(stored, 42)
    parsed = urlsplit(signed)
    query = parse_qs(parsed.query)

    assert parsed.path == stored
    assert query["customer_id"] == ["42"]
    expires = int(query["expires"][0])
    signature = query["signature"][0]
    assert verify_private_avatar_signature(
        customer_id=42,
        key="avatars/abcdef0123456789abcdef0123456789.webp",
        expires=expires,
        signature=signature,
    )
    assert not verify_private_avatar_signature(
        customer_id=43,
        key="avatars/abcdef0123456789abcdef0123456789.webp",
        expires=expires,
        signature=signature,
    )
    assert not verify_private_avatar_signature(
        customer_id=42,
        key="avatars/abcdef0123456789abcdef0123456789.webp",
        expires=expires,
        signature=signature + "tampered",
    )
    assert not verify_private_avatar_signature(
        customer_id=42,
        key="avatars/abcdef0123456789abcdef0123456789.webp",
        expires=expires,
        signature=signature,
        now=expires,
    )


def test_private_avatar_key_parser_rejects_other_buckets_and_paths(monkeypatch):
    monkeypatch.setattr("app.services.private_media.get_settings", _settings)
    assert avatar_object_key(
        "s3://private-bucket/avatars/abcdef0123456789abcdef0123456789.webp"
    ) == "avatars/abcdef0123456789abcdef0123456789.webp"
    assert avatar_object_key(
        "s3://attacker-bucket/avatars/abcdef0123456789abcdef0123456789.webp"
    ) is None
    assert avatar_object_key(
        "/uploads/avatars/abcdef0123456789abcdef0123456789.webp"
    ) == "avatars/abcdef0123456789abcdef0123456789.webp"
    assert avatar_object_key("/api/media/private/avatars/../secret.webp") is None


@pytest.mark.asyncio
async def test_private_avatar_requires_signature_and_current_customer_reference(
    db_session, monkeypatch
):
    monkeypatch.setattr("app.services.private_media.get_settings", _settings)
    monkeypatch.setattr("app.api.media.get_settings", _settings)
    key = "avatars/abcdef0123456789abcdef0123456789.webp"
    stored = f"/api/media/private/{key}"
    customer = Customer(customer_no="PRIVATE-MEDIA-TEST-01", avatar_url=stored, status=1)
    db_session.add(customer)
    await db_session.flush()

    class FakeStorage:
        async def get_bytes(self, requested_key):
            assert requested_key == key
            return b"private-avatar"

    monkeypatch.setattr("app.api.media.get_storage", lambda: FakeStorage())
    signed = sign_private_avatar_url(stored, customer.id)
    query = parse_qs(urlsplit(signed).query)
    response = await get_private_avatar(
        filename=key.split("/", 1)[1],
        customer_id=customer.id,
        expires=int(query["expires"][0]),
        signature=query["signature"][0],
        db=db_session,
    )
    assert response.body == b"private-avatar"
    assert response.media_type == "image/webp"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"

    customer.avatar_url = None
    await db_session.flush()
    with pytest.raises(AppException) as exc:
        await get_private_avatar(
            filename=key.split("/", 1)[1],
            customer_id=customer.id,
            expires=int(query["expires"][0]),
            signature=query["signature"][0],
            db=db_session,
        )
    assert exc.value.status_code == 404



@pytest.mark.asyncio
async def test_local_avatar_upload_uses_private_media_route(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.object_storage.get_settings",
        lambda: SimpleNamespace(upload_dir=str(tmp_path)),
    )
    storage = LocalObjectStorage()
    key = "avatars/abcdef0123456789abcdef0123456789.webp"

    url = await storage.put_bytes(key, b"local-private-avatar", "image/webp")

    assert url == f"/api/media/private/{key}"
    assert (tmp_path / key).read_bytes() == b"local-private-avatar"



def test_signing_helper_never_returns_raw_s3_or_malformed_private_reference(monkeypatch):
    monkeypatch.setattr("app.services.private_media.get_settings", _settings)
    from app.services.private_media import sign_private_avatar_url

    assert sign_private_avatar_url("s3://wrong-bucket/avatars/not-an-image", 42) is None
    assert sign_private_avatar_url("/api/media/private/avatars/../secret.webp", 42) is None



def test_signing_secret_uses_configured_jwt_private_key_for_asymmetric_algorithms(monkeypatch):
    monkeypatch.setattr(
        "app.services.private_media.get_settings",
        lambda: SimpleNamespace(
            jwt_algorithm="RS256",
            jwt_secret="",
            jwt_private_key="-----BEGIN PRIVATE KEY-----" + ("x" * 64),
        ),
    )
    from app.services.private_media import _signing_secret

    assert _signing_secret().startswith(b"-----BEGIN PRIVATE KEY-----")
