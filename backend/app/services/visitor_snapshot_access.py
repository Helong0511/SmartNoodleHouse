"""访客人脸抓拍的短时签名访问控制。"""
from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

from app.core.config import get_settings

_URL_TTL_SECONDS = 300
_DOMAIN = b"SmartNoodleHouse:visitor-snapshot-url:v1\x00"


def _key_materials() -> list[bytes]:
    settings = get_settings()
    values = [
        (getattr(settings, "secret_encryption_key", None) or "").strip(),
        (getattr(settings, "secret_encryption_key_previous", None) or "").strip(),
        (getattr(settings, "face_encryption_key", None) or "").strip(),
        (getattr(settings, "jwt_secret", None) or "").strip(),
    ]
    materials: list[bytes] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            materials.append(hashlib.sha256(_DOMAIN + value.encode("utf-8")).digest())
    return materials


def _message(event_id: int, store_id: int, object_key: str, expires_at: int) -> bytes:
    return f"{int(event_id)}\n{int(store_id)}\n{object_key}\n{int(expires_at)}".encode("utf-8")


def build_visitor_snapshot_url(
    event_id: int,
    store_id: int,
    object_key: str,
    *,
    now: int | None = None,
) -> str:
    """生成 5 分钟有效的签名 URL；URL 不包含真实对象路径。"""
    materials = _key_materials()
    if not materials:
        raise RuntimeError("缺少访客抓拍 URL 签名密钥")
    issued = int(time.time() if now is None else now)
    expires_at = issued + _URL_TTL_SECONDS
    signature = hmac.new(
        materials[0],
        _message(event_id, store_id, object_key, expires_at),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode({"exp": expires_at, "sig": signature})
    return f"/api/cameras/visitors/snapshot/{int(event_id)}?{query}"


def verify_visitor_snapshot_signature(
    event_id: int,
    store_id: int,
    object_key: str,
    expires_at: int,
    signature: str,
    *,
    now: int | None = None,
) -> bool:
    """验证签名、过期时间和最大未来窗口；支持密钥轮换中的上一把密钥。"""
    current = int(time.time() if now is None else now)
    try:
        exp = int(expires_at)
    except (TypeError, ValueError):
        return False
    if exp < current or exp > current + _URL_TTL_SECONDS + 30:
        return False
    if not signature or len(str(signature)) != 64:
        return False
    message = _message(event_id, store_id, object_key, exp)
    for material in _key_materials():
        expected = hmac.new(material, message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, str(signature).lower()):
            return True
    return False
