"""支付宝小程序：手机号密文解密等。"""
from __future__ import annotations

import base64
import json
import logging
import re

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


def _decode_aes_key(raw: str) -> bytes:
    key = (raw or "").strip()
    if not key:
        raise AppException(40054, "未配置 ALIPAY_AES_KEY，无法解密支付宝手机号")
    try:
        decoded = base64.b64decode(key)
    except Exception as exc:
        raise AppException(40054, "ALIPAY_AES_KEY 格式无效") from exc
    if len(decoded) not in (16, 24, 32):
        raise AppException(40054, "ALIPAY_AES_KEY 长度无效")
    return decoded


def decrypt_alipay_content(encrypted_b64: str, aes_key: str | None = None) -> str:
    """解密支付宝接口内容加密（AES/CBC/PKCS7，IV 为 16 字节 0）。"""
    settings = get_settings()
    key = _decode_aes_key(aes_key or settings.alipay_aes_key)
    try:
        ciphertext = base64.b64decode((encrypted_b64 or "").strip())
    except Exception as exc:
        raise AppException(40054, "支付宝手机号密文无效") from exc
    iv = bytes(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")


def decrypt_phone_response(encrypted_b64: str) -> str:
    """my.getPhoneNumber 返回的 response 解密并提取手机号。"""
    if not (encrypted_b64 or "").strip():
        raise AppException(40054, "缺少支付宝手机号密文")
    settings = get_settings()
    if settings.is_development and not (settings.alipay_aes_key or "").strip():
        mock = re.search(r"1\d{10}", encrypted_b64)
        if mock:
            logger.warning("开发环境未配置 ALIPAY_AES_KEY，从密文占位解析手机号（仅联调）")
            return mock.group(0)
        raise AppException(40054, "开发环境请配置 ALIPAY_AES_KEY 或改用手动输入手机号")
    plain = decrypt_alipay_content(encrypted_b64)
    try:
        data = json.loads(plain)
    except json.JSONDecodeError:
        phone = re.search(r"1\d{10}", plain)
        if phone:
            return phone.group(0)
        raise AppException(40054, "支付宝手机号解密结果无效")
    if isinstance(data, dict):
        code = str(data.get("code") or "")
        if code and code not in ("10000", "200"):
            msg = data.get("msg") or data.get("subMsg") or "解密失败"
            raise AppException(40054, f"支付宝手机号获取失败: {msg}")
        mobile = (
            data.get("mobile")
            or data.get("phoneNumber")
            or (data.get("data") or {}).get("mobile")
            or ""
        )
        mobile = str(mobile).strip()
        if mobile:
            return mobile
    phone = re.search(r"1\d{10}", plain)
    if phone:
        return phone.group(0)
    raise AppException(40054, "未能从支付宝响应中解析手机号")
