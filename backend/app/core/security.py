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


# JWT 标准/内部声明：extra 不得覆盖，防止提权或伪造 typ/sub/exp
_JWT_RESERVED_CLAIMS = frozenset(
    {
        "sub",
        "exp",
        "iat",
        "nbf",
        "jti",
        "iss",
        "aud",
        "typ",
        "ver",
        "cam",
        "store",
    }
)


def _safe_extra_claims(extra: dict[str, Any] | None) -> dict[str, Any]:
    if not extra:
        return {}
    return {k: v for k, v in extra.items() if k not in _JWT_RESERVED_CLAIMS}


def create_access_token(
    subject: str | int,
    extra: dict[str, Any] | None = None,
    *,
    token_version: int = 0,
) -> str:
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    now = datetime.now(timezone.utc)
    ttl = SystemSettingsService.resolved_int_sync(
        "jwt_expire_minutes", settings.jwt_expire_minutes
    )
    expire = now + timedelta(minutes=max(5, int(ttl)))
    payload: dict[str, Any] = {}
    if extra:
        payload.update(_safe_extra_claims(extra))
    # 标准声明最后写入，确保不可被 extra 覆盖
    payload.update(
        {
            "sub": str(subject),
            "exp": expire,
            "iat": now,
            "nbf": now,
            "jti": uuid4().hex,
            "iss": settings.app_name,
            "aud": settings.jwt_audience,
            "typ": "staff",
            "ver": token_version,
        }
    )
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_stream_ticket(user_id: int, camera_id: int, *, store_id: int | None = None, token_version: int = 0, ttl_seconds: int = 120) -> str:
    """摄像头预览短期票据，避免把长生命周期 JWT 放进 URL query。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    # 流票据强制短时：上限 300 秒，默认 120
    ttl = max(15, min(int(ttl_seconds or 120), 300))
    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(seconds=ttl),
        "iat": now,
        "nbf": now,
        "jti": uuid4().hex,
        "iss": settings.app_name,
        "aud": settings.jwt_audience,
        "typ": "stream",
        "cam": int(camera_id),
        "ver": token_version,
    }
    # P2-4 编码 store_id，消费端二次校验票据门店与摄像头门店一致
    if store_id is not None:
        payload["store"] = int(store_id)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_customer_token(
    customer_id: int,
    channel: str = "wechat",
    *,
    token_version: int = 0,
) -> str:
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    now = datetime.now(timezone.utc)
    ttl = SystemSettingsService.resolved_int_sync(
        "customer_jwt_expire_minutes",
        int(getattr(settings, "customer_jwt_expire_minutes", None) or settings.jwt_expire_minutes),
    )
    # 顾客令牌独立生命周期；下限 5 分钟，与配置一致（不再强制 30 分钟）
    expire = now + timedelta(minutes=max(5, int(ttl)))
    # 渠道仅允许微信/支付宝，防止客户端伪造任意 channel 写入令牌
    ch = (channel or "wechat").strip().lower()
    if ch not in ("wechat", "alipay"):
        ch = "wechat"
    payload = {
        "sub": str(customer_id),
        "exp": expire,
        "iat": now,
        "nbf": now,
        "jti": uuid4().hex,
        "iss": settings.app_name,
        "aud": settings.jwt_audience,
        "typ": "customer",
        "channel": ch,
        "ver": int(token_version or 0),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_customer_token(token: str, *, leeway_seconds: int = 0) -> dict[str, Any]:
    payload = decode_token(token, leeway_seconds=leeway_seconds)
    if payload.get("typ") != "customer":
        raise JWTError("非顾客令牌")
    return payload


def decode_token(token: str, *, leeway_seconds: int = 0) -> dict[str, Any]:
    """leeway_seconds：过期容差秒数（P2-6 宽限刷新用）。require_exp 保持开启，
    exp 声明缺失仍拒绝；leeway 仅放宽「已过期但在容差窗口内」的判定。"""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
        audience=settings.jwt_audience,
        options={
            "require_exp": True,
            "require_iat": True,
            "require_sub": True,
            "require_nbf": True,
            "require_aud": True,
            "leeway": int(leeway_seconds or 0),
        },
    )
