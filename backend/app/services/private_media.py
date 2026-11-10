"""Short-lived, database-backed delivery URLs for private customer avatars."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from urllib.parse import urlencode

from app.core.config import get_settings

_AVATAR_NAME_RE = re.compile(r"[0-9a-f]{32}\.(?:webp|jpg|jpeg|png|gif)")
_PRIVATE_PATH_RE = re.compile(
    r"^/api/media/private/avatars/([0-9a-f]{32}\.(?:webp|jpg|jpeg|png|gif))$"
)
_DEFAULT_TTL_SECONDS = 300
_MAX_TTL_SECONDS = 300


def avatar_object_key(value: str | None) -> str | None:
    """Extract only an application-generated avatar key; never accept arbitrary S3 URLs."""
    raw = str(value or "").strip()
    match = _PRIVATE_PATH_RE.fullmatch(raw)
    if match:
        return f"avatars/{match.group(1)}"
    legacy_local = re.fullmatch(
        r"/uploads/avatars/([0-9a-f]{32}\.(?:webp|jpg|jpeg|png|gif))", raw
    )
    if legacy_local:
        return f"avatars/{legacy_local.group(1)}"
    if raw.startswith("s3://"):
        settings = get_settings()
        prefix = f"s3://{str(settings.s3_bucket or '').strip()}/avatars/"
        if raw.startswith(prefix):
            filename = raw[len(prefix):]
            if _AVATAR_NAME_RE.fullmatch(filename):
                return f"avatars/{filename}"
    return None


def private_avatar_path(key: str) -> str:
    if not key.startswith("avatars/") or not _AVATAR_NAME_RE.fullmatch(key.split("/", 1)[-1]):
        raise ValueError("非法头像对象键")
    return f"/api/media/private/{key}"


def _signing_secret() -> bytes:
    settings = get_settings()
    algorithm = str(getattr(settings, "jwt_algorithm", "HS256") or "HS256").upper()
    if algorithm in ("RS256", "EDDSA") or algorithm.startswith("ES"):
        raw_secret = getattr(settings, "jwt_private_key", "") or ""
    else:
        raw_secret = getattr(settings, "jwt_secret", "") or ""
    secret = str(raw_secret).encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("私有媒体签名要求配置有效的 JWT 签名密钥（HS256 secret 或非对称 private key）")
    return secret


def _signature(customer_id: int, key: str, expires: int) -> str:
    message = f"private-avatar:v1:{int(customer_id)}:{key}:{int(expires)}".encode("utf-8")
    return hmac.new(_signing_secret(), message, hashlib.sha256).hexdigest()


def sign_private_avatar_url(
    value: str | None,
    customer_id: int,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str | None:
    """Turn a stored private avatar reference into a short-lived bearer URL."""
    raw = str(value or "").strip()
    key = avatar_object_key(raw)
    if not key:
        # Never leak a raw S3 URI or malformed internal private-media path to API clients.
        if raw.startswith(("s3://", "/api/media/private/avatars/")):
            return None
        return value
    ttl = max(30, min(int(ttl_seconds), _MAX_TTL_SECONDS))
    expires = int(time.time()) + ttl
    path = private_avatar_path(key)
    query = urlencode(
        {
            "customer_id": int(customer_id),
            "expires": expires,
            "signature": _signature(int(customer_id), key, expires),
        }
    )
    return f"{path}?{query}"


def verify_private_avatar_signature(
    *, customer_id: int, key: str, expires: int, signature: str, now: int | None = None
) -> bool:
    """Validate a short-lived signature and reject malformed or overlong grants."""
    filename = key.split("/", 1)[-1] if key.startswith("avatars/") else ""
    if not _AVATAR_NAME_RE.fullmatch(filename):
        return False
    current = int(time.time()) if now is None else int(now)
    if int(expires) <= current or int(expires) > current + _MAX_TTL_SECONDS + 30:
        return False
    try:
        expected = _signature(int(customer_id), key, int(expires))
    except (RuntimeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(signature or ""))
