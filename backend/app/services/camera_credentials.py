"""摄像头凭据加密存储"""
from __future__ import annotations

from urllib.parse import quote, urlparse, urlunparse

from app.services.face_service import FaceEncryption


def encrypt_password(password: str) -> str:
    enc = FaceEncryption()
    return enc.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    enc = FaceEncryption()
    return enc.decrypt(encrypted.encode()).decode()


def mask_rtsp_url(source_url: str) -> str:
    """列表展示时隐藏密码"""
    try:
        parsed = urlparse(source_url)
        if parsed.password:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            netloc = f"{parsed.username or '***'}:***@{host}" if "@" in source_url else parsed.netloc
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return source_url


def build_rtsp_url(host: str, username: str, password: str, channel: str = "102") -> str:
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return f"rtsp://{user}:{pwd}@{host}/Streaming/Channels/{channel}"
