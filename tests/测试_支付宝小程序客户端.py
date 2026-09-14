"""支付宝小程序手机号解密测试。"""
import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.core.exceptions import AppException
from app.services.alipay_mini_client import decrypt_phone_response


def _encrypt_phone(phone: str, aes_key_b64: str) -> str:
    key = base64.b64decode(aes_key_b64)
    iv = bytes(16)
    plain = json.dumps({"code": "10000", "mobile": phone}, ensure_ascii=False).encode("utf-8")
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def _settings(aes_key: str, *, is_development: bool = False):
    class _Settings:
        pass

    s = _Settings()
    s.is_development = is_development
    s.alipay_aes_key = aes_key
    return s


def test_decrypt_phone_response(monkeypatch):
    aes_key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    encrypted = _encrypt_phone("13800138000", aes_key)
    monkeypatch.setattr("app.services.alipay_mini_client.get_settings", lambda: _settings(aes_key))
    assert decrypt_phone_response(encrypted) == "13800138000"


def test_decrypt_phone_response_envelope(monkeypatch):
    """前端常把完整报文（含 response/sign）原样上传。"""
    aes_key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    cipher = _encrypt_phone("13900139000", aes_key)
    envelope = json.dumps({"response": cipher, "sign": "fake-sign", "sign_type": "RSA2"})
    monkeypatch.setattr("app.services.alipay_mini_client.get_settings", lambda: _settings(aes_key))
    assert decrypt_phone_response(envelope) == "13900139000"


def test_decrypt_phone_insufficient_permissions(monkeypatch):
    monkeypatch.setattr(
        "app.services.alipay_mini_client.get_settings",
        lambda: _settings(base64.b64encode(b"0123456789abcdef").decode("ascii")),
    )
    with pytest.raises(AppException) as ei:
        decrypt_phone_response(json.dumps({"code": "40006", "msg": "Insufficient Permissions"}))
    assert "获取会员手机号" in ei.value.message


def test_decrypt_phone_wrong_key_no_500(monkeypatch):
    aes_key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    other = base64.b64encode(b"fedcba9876543210").decode("ascii")
    encrypted = _encrypt_phone("13800138000", aes_key)
    monkeypatch.setattr("app.services.alipay_mini_client.get_settings", lambda: _settings(other))
    with pytest.raises(AppException) as ei:
        decrypt_phone_response(encrypted)
    assert "解密失败" in ei.value.message or "ALIPAY_AES_KEY" in ei.value.message
