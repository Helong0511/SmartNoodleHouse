"""金额精度、JWT aud、密钥派生、Leader 锁等回归。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_money_half_up():
    from app.core.response import money

    assert money("1.005") == Decimal("1.01")
    assert money("1.004") == Decimal("1.00")
    assert money("-1.005") == Decimal("-1.01")


def test_jwt_audience_roundtrip():
    from app.core.security import create_access_token, create_customer_token, decode_token

    with patch("app.core.security.get_settings") as gs:
        settings = gs.return_value
        settings.jwt_secret = "x" * 48
        settings.jwt_algorithm = "HS256"
        settings.jwt_expire_minutes = 60
        settings.customer_jwt_expire_minutes = 120
        settings.app_name = "SmartNoodleHouse"
        settings.jwt_audience = "smart-noodle-house"

        staff = create_access_token(1)
        payload = decode_token(staff)
        assert payload["aud"] == "smart-noodle-house"
        assert payload["typ"] == "staff"

        cust = create_customer_token(9, "wechat")
        cp = decode_token(cust)
        assert cp["typ"] == "customer"
        assert cp["aud"] == "smart-noodle-house"


def test_jwt_rejects_wrong_audience():
    from jose import jwt

    from app.core.security import decode_token

    with patch("app.core.security.get_settings") as gs:
        settings = gs.return_value
        settings.jwt_secret = "y" * 48
        settings.jwt_algorithm = "HS256"
        settings.app_name = "SmartNoodleHouse"
        settings.jwt_audience = "smart-noodle-house"
        bad = jwt.encode(
            {
                "sub": "1",
                "exp": 9999999999,
                "iat": 1,
                "nbf": 1,
                "iss": "SmartNoodleHouse",
                "aud": "other-app",
                "typ": "staff",
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        with pytest.raises(Exception):
            decode_token(bad)


def test_face_encryption_sha256_and_legacy_compat():
    from app.services.face_service import FaceEncryption, _fernet_from_material

    material = "test-face-key-32-bytes-minimum!!"
    with patch("app.services.face_service.get_settings") as gs:
        gs.return_value.face_encryption_key = material
        enc = FaceEncryption()
        blob = enc.encrypt(b"hello-face")
        assert enc.decrypt(blob) == b"hello-face"

        # 旧派生密文仍可解
        legacy = _fernet_from_material(material, legacy=True)
        old_blob = legacy.encrypt(b"legacy-data")
        assert enc.decrypt(old_blob) == b"legacy-data"


def test_secret_encryption_sha256_roundtrip():
    from app.services.secret_encryption import SecretEncryptionService, _fernet_from_material

    with patch("app.services.secret_encryption.get_settings") as gs:
        gs.return_value.secret_encryption_key = "secret-enc-key-32bytes-minimum!!"
        gs.return_value.face_encryption_key = ""
        cipher = SecretEncryptionService.encrypt("platform-token-xyz")
        assert cipher and cipher.startswith("snhenc:")
        assert SecretEncryptionService.decrypt(cipher) == "platform-token-xyz"

        # 旧派生密文仍可解
        legacy = _fernet_from_material(b"secret-enc-key-32bytes-minimum!!", legacy=True)
        old_tok = legacy.encrypt(b"old-platform-token").decode()
        old_cipher = f"snhenc:v1:{old_tok}"
        assert SecretEncryptionService.decrypt(old_cipher) == "old-platform-token"


@pytest.mark.asyncio
async def test_leader_lock_acquire_and_hold():
    from app.core import leader_lock

    fake = MagicMock()
    fake.set = AsyncMock(return_value=True)
    fake.get = AsyncMock(return_value=b"w1")
    fake.expire = AsyncMock(return_value=True)
    fake.delete = AsyncMock(return_value=1)
    fake.eval = AsyncMock(return_value=True)  # Lua compare-and-expire 续期成功

    with patch("app.core.leader_lock.get_redis", AsyncMock(return_value=fake)):
        assert await leader_lock.try_acquire_leader("w1") is True
        fake.set = AsyncMock(return_value=False)
        assert await leader_lock.try_acquire_leader("w1") is True  # 已持有则续期
        fake.get = AsyncMock(return_value=b"other")
        assert await leader_lock.try_acquire_leader("w1") is False
        await leader_lock.release_leader("w1")  # other 持有，不应 delete
        fake.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_leader_lock_redis_fail_closed():
    from app.core import leader_lock

    with patch("app.core.leader_lock.get_redis", AsyncMock(side_effect=RuntimeError("down"))):
        assert await leader_lock.try_acquire_leader("w1") is False
