from __future__ import annotations

from types import SimpleNamespace

from app.services.camera_credentials import decrypt_password, encrypt_password
from app.services.face_service import FaceEncryption
from app.services.secret_encryption import SecretEncryptionService


def _settings(secret_key: str, face_key: str, previous: str = ""):
    return SimpleNamespace(
        secret_encryption_key=secret_key,
        face_encryption_key=face_key,
        face_encryption_key_previous=previous,
    )


def test_new_camera_password_uses_independent_secret_encryption_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: _settings("secret-key-material-0123456789-abcdef", "face-key-material-0123456789-abcdef"),
    )
    encrypted = encrypt_password("camera-password-123")
    assert encrypted.startswith("snhenc:v1:")
    assert decrypt_password(encrypted) == "camera-password-123"


def test_new_camera_password_is_not_dependent_on_face_key(monkeypatch):
    secret = "secret-key-material-0123456789-abcdef"
    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: _settings(secret, "face-key-old-material-0123456789"),
    )
    encrypted = encrypt_password("camera-password-456")

    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: _settings(secret, "face-key-rotated-material-012345"),
    )
    assert decrypt_password(encrypted) == "camera-password-456"


def test_legacy_face_encrypted_camera_password_remains_readable(monkeypatch):
    old_face_key = "face-key-old-material-0123456789"
    monkeypatch.setattr(
        "app.services.face_service.get_settings",
        lambda: _settings("", old_face_key),
    )
    legacy = FaceEncryption().encrypt(b"legacy-camera-password").decode()

    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: _settings("secret-key-material-0123456789-abcdef", "new-face-key-material-0123456789"),
    )
    monkeypatch.setattr(
        "app.services.face_service.get_settings",
        lambda: _settings("secret-key-material-0123456789-abcdef", "new-face-key-material-0123456789", old_face_key),
    )
    assert decrypt_password(legacy) == "legacy-camera-password"



def test_business_secret_previous_key_keeps_old_ciphertext_readable(monkeypatch):
    old_key = "business-secret-old-material-012345"
    new_key = "business-secret-new-material-012345"
    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: _settings(old_key, "face-key-material-0123456789"),
    )
    old_ciphertext = SecretEncryptionService.encrypt("delivery-access-token")

    monkeypatch.setattr(
        "app.services.secret_encryption.get_settings",
        lambda: SimpleNamespace(
            secret_encryption_key=new_key,
            secret_encryption_key_previous=old_key,
            face_encryption_key="face-key-material-0123456789",
        ),
    )
    assert SecretEncryptionService.decrypt(old_ciphertext) == "delivery-access-token"
    # 新写入始终使用当前密钥；当前密钥可以独立解密。
    new_ciphertext = SecretEncryptionService.encrypt("new-token")
    assert SecretEncryptionService.decrypt(new_ciphertext) == "new-token"
