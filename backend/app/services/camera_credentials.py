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


def build_connect_source(
    source_url: str,
    username: str | None,
    password_encrypted: str | None,
) -> str:
    """
    连接时用解密密码重建 RTSP。
    - USB 数字源原样返回
    - 已含明文密码的 URL：优先用加密字段覆盖
    - 仅 host 路径：用 username + 解密密码拼装
    """
    if not source_url or source_url.isdigit():
        return source_url
    if not password_encrypted:
        return source_url
    try:
        password = decrypt_password(password_encrypted)
    except Exception:
        return source_url

    parsed = urlparse(source_url)
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return source_url

    user = username or parsed.username or "admin"
    host = parsed.hostname or ""
    if not host:
        return source_url
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}{port}"
    return urlunparse((parsed.scheme, netloc, path, "", parsed.query, ""))
