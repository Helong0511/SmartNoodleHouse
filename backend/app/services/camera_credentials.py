"""摄像头凭据加密存储"""
from __future__ import annotations

from urllib.parse import quote, unquote, urlparse, urlunparse

from app.core.exceptions import AppException
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
        if parsed.password or (parsed.username and "@" in (parsed.netloc or "")):
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            user = parsed.username or "***"
            netloc = f"{user}:***@{host}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return source_url


def extract_url_credentials(source_url: str) -> tuple[str, str | None, str | None]:
    """
    从 URL 拆出口令并返回无明文密码的 source。
    返回 (safe_url, username, password)。
    """
    if not source_url or source_url.isdigit():
        return source_url, None, None
    try:
        parsed = urlparse(source_url)
    except Exception:
        return source_url, None, None
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return source_url, None, None
    user = unquote(parsed.username) if parsed.username else None
    pwd = unquote(parsed.password) if parsed.password else None
    if not user and not pwd:
        return source_url, None, None
    host = parsed.hostname or ""
    if not host:
        raise AppException(40025, "摄像头地址缺少主机名")
    port = f":{parsed.port}" if parsed.port else ""
    # 禁止把明文密码留在 source_url：仅保留用户名@host（或纯 host）
    if user:
        netloc = f"{quote(user, safe='')}@{host}{port}"
    else:
        netloc = f"{host}{port}"
    safe = urlunparse((parsed.scheme, netloc, parsed.path or "", "", parsed.query, ""))
    return safe, user, pwd


def ensure_no_plaintext_password_in_url(source_url: str) -> None:
    """禁止 rtsp://user:pass@... 明文入库。"""
    if not source_url or "://" not in source_url:
        return
    try:
        parsed = urlparse(source_url)
    except Exception:
        return
    if parsed.password:
        raise AppException(
            40026,
            "禁止在 source_url 中明文保存密码，请将密码填入 password 字段（将加密存储）",
        )


def prepare_camera_source(
    source_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, str | None, str | None]:
    """
    规范化摄像头源：URL 内嵌口令拆到 password_encrypted；source_url 不含明文密码。
    返回 (safe_url, username, password_plaintext_to_encrypt)。
    """
    safe, url_user, url_pwd = extract_url_credentials(source_url)
    user = (username or url_user or None)
    pwd = password if password is not None and password != "" else url_pwd
    ensure_no_plaintext_password_in_url(safe)
    # 二次保险：若仍含 :xxx@ 形态则强制剥离
    try:
        parsed = urlparse(safe)
        if parsed.password:
            safe, u2, p2 = extract_url_credentials(safe)
            user = user or u2
            pwd = pwd or p2
    except Exception:
        pass
    ensure_no_plaintext_password_in_url(safe)
    return safe, user, pwd


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
        # 历史脏数据：连接时临时解析 URL 内嵌口令，不再向外抛错阻断
        try:
            safe, user, pwd = extract_url_credentials(source_url)
            if pwd:
                parsed = urlparse(safe)
                host = parsed.hostname or ""
                if host and parsed.scheme in ("rtsp", "rtsps", "http", "https"):
                    port = f":{parsed.port}" if parsed.port else ""
                    u = username or user or "admin"
                    netloc = f"{quote(u, safe='')}:{quote(pwd, safe='')}@{host}{port}"
                    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", parsed.query, ""))
        except Exception:
            pass
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
