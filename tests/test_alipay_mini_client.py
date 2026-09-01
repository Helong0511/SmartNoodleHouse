"""支付宝小程序手机号解密测试。"""
import base64
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

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


def test_decrypt_phone_response(monkeypatch):
    aes_key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    encrypted = _encrypt_phone("13800138000", aes_key)

    class _Settings:
        is_development = False
        alipay_aes_key = aes_key

    monkeypatch.setattr("app.services.alipay_mini_client.get_settings", lambda: _Settings())
    assert decrypt_phone_response(encrypted) == "13800138000"
