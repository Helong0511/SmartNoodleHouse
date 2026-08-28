from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(
    subject: str | int,
    extra: dict[str, Any] | None = None,
    *,
    token_version: int = 0,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(subject),
        "exp": expire,
        "iat": now,
        "nbf": now,
        "jti": uuid4().hex,
        "iss": settings.app_name,
        "typ": "staff",
        "ver": token_version,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_stream_ticket(user_id: int, camera_id: int, *, token_version: int = 0, ttl_seconds: int = 120) -> str:
    """摄像头预览短期票据，避免把长生命周期 JWT 放进 URL query。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(seconds=ttl_seconds),
        "iat": now,
        "nbf": now,
        "jti": uuid4().hex,
        "iss": settings.app_name,
        "typ": "stream",
        "cam": int(camera_id),
        "ver": token_version,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_customer_token(
    customer_id: int,
    channel: str = "wechat",
    *,
    token_version: int = 0,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expire_minutes * 7)
    payload = {
        "sub": str(customer_id),
        "exp": expire,
        "iat": now,
        "nbf": now,
        "jti": uuid4().hex,
        "iss": settings.app_name,
        "typ": "customer",
        "channel": channel,
        "ver": int(token_version or 0),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_customer_token(token: str) -> dict[str, Any]:
    payload = decode_token(token)
    if payload.get("typ") != "customer":
        raise JWTError("非顾客令牌")
    return payload


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require_exp": True, "require_iat": True, "require_sub": True},
    )
