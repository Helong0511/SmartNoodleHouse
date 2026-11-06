"""通用密钥加密（平台 Token 等）：密文带 key version 前缀，便于轮换。"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CURRENT_KEY_VERSION = "v1"
_PREFIX = "snhenc:"


def _fernet_from_material(material: bytes | str, *, legacy: bool = False) -> Fernet:
    """密钥派生：SHA256(material)；legacy 兼容历史「截断+右侧补0」。接受 bytes 或 str。"""
    if isinstance(material, str):
        material = (material or "").encode("utf-8")
    if legacy:
        key = material[:32].ljust(32, b"0")
    else:
        key = hashlib.sha256(material).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class SecretEncryptionService:
    """
    加密格式：`snhenc:v1:<fernet_token>`。
    明文（历史数据）原样返回；解密失败时记录日志并返回 None。
    密钥派生：SHA256(material)；解密兼容历史「截断+右侧补0」。
    """

    @classmethod
    def _material(cls) -> bytes:
        settings = get_settings()
        raw = (settings.secret_encryption_key or settings.face_encryption_key or "").encode()
        if not raw:
            raise RuntimeError("未配置 SECRET_ENCRYPTION_KEY / FACE_ENCRYPTION_KEY，无法加解密")
        return raw

    @classmethod
    def _fernet(cls, version: str = CURRENT_KEY_VERSION) -> Fernet:
        if version != CURRENT_KEY_VERSION:
            raise ValueError(f"不支持的密钥版本: {version}")
        return _fernet_from_material(cls._material(), legacy=False)

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
            return text
        try:
            _, version, token = text.split(":", 2)
        except ValueError:
            logger.warning("密文格式异常，无法解密")
            return None
        if version != CURRENT_KEY_VERSION:
            logger.warning("不支持的业务密文版本: %s", version)
            return None

        settings = get_settings()
        materials = [
            (settings.secret_encryption_key or settings.face_encryption_key or "").strip(),
            (getattr(settings, "secret_encryption_key_previous", None) or "").strip(),
        ]
        # 先尝试当前密钥，再尝试上一把密钥；兼容旧版「截断+右侧补0」派生。
        for material in dict.fromkeys(value for value in materials if value):
            try:
                return _fernet_from_material(material, legacy=False).decrypt(token.encode()).decode()
            except InvalidToken:
                try:
                    return _fernet_from_material(material, legacy=True).decrypt(token.encode()).decode()
                except InvalidToken:
                    continue
                except (ValueError, RuntimeError) as exc:
                    logger.warning("业务密文旧派生解密失败 version=%s: %s", version, exc)
                    continue
            except (ValueError, RuntimeError) as exc:
                logger.warning("业务密文解密失败 version=%s: %s", version, exc)
                continue

        logger.warning("业务密文解密失败：当前密钥与上一把密钥均无法解密 version=%s", version)
        return None

    @classmethod
    def key_version(cls, stored: str | None) -> str | None:
        if not cls.is_encrypted(stored):
            return None
        try:
            return str(stored).split(":", 2)[1]
        except (IndexError, AttributeError):
            return None
