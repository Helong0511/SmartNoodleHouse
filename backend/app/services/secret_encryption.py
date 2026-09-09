"""通用密钥加密（平台 Token 等）：密文带 key version 前缀，便于轮换。"""
from __future__ import annotations

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CURRENT_KEY_VERSION = "v1"
_PREFIX = "snhenc:"


class SecretEncryptionService:
    """
    加密格式：`snhenc:v1:<fernet_token>`。
    明文（历史数据）原样返回；解密失败时记录日志并返回 None。
    """

    @classmethod
    def _fernet(cls, version: str = CURRENT_KEY_VERSION) -> Fernet:
        settings = get_settings()
        raw = (settings.secret_encryption_key or settings.face_encryption_key or "").encode()
        if not raw:
            raise RuntimeError("未配置 SECRET_ENCRYPTION_KEY / FACE_ENCRYPTION_KEY，无法加解密")
        # 预留多版本：当前仅 v1 使用同一材料派生
        if version != CURRENT_KEY_VERSION:
            raise ValueError(f"不支持的密钥版本: {version}")
        key = raw[:32].ljust(32, b"0")
        return Fernet(base64.urlsafe_b64encode(key))

    @classmethod
    def is_encrypted(cls, value: str | None) -> bool:
        return bool(value and str(value).startswith(_PREFIX))

    @classmethod
    def encrypt(cls, plaintext: str | None, *, version: str = CURRENT_KEY_VERSION) -> str | None:
        if plaintext is None:
            return None
        text = str(plaintext)
        if text == "" or cls.is_encrypted(text):
            return text
        token = cls._fernet(version).encrypt(text.encode()).decode()
        return f"{_PREFIX}{version}:{token}"

    @classmethod
    def decrypt(cls, stored: str | None) -> str | None:
        if stored is None:
            return None
        text = str(stored)
        if text == "":
            return ""
        if not cls.is_encrypted(text):
            # 兼容历史明文
            return text
        try:
            _, version, token = text.split(":", 2)
        except ValueError:
            logger.warning("密文格式异常，无法解密")
            return None
        try:
            return cls._fernet(version).decrypt(token.encode()).decode()
        except (InvalidToken, ValueError, RuntimeError) as exc:
            logger.warning("密文解密失败 version=%s: %s", version, exc)
            return None

    @classmethod
    def key_version(cls, stored: str | None) -> str | None:
        if not cls.is_encrypted(stored):
            return None
        try:
            return str(stored).split(":", 2)[1]
        except (IndexError, AttributeError):
            return None
