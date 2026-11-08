from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from asyncio import Semaphore, get_running_loop
from threading import local

from starlette.concurrency import run_in_threadpool
from argon2.exceptions import InvalidHashError as Argon2InvalidHashError
from jwt import InvalidTokenError as JWTError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import get_settings

# 新密码使用 Argon2id；BcryptHasher 只负责兼容历史 Passlib/bcrypt 哈希。
# 登录成功后通过 verify_and_update 渐进升级，避免强制全体员工重置密码。
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher(rounds=12)))

# Argon2 默认约 64 MiB 内存成本；每个事件循环最多并发 2 次，避免登录洪峰放大内存峰值。
_PASSWORD_HASH_LIMITER_STATE = local()


def _password_hash_limiter() -> Semaphore:
    """每个事件循环线程独立限流，避免跨 loop 复用已绑定的 Semaphore。"""
    loop = get_running_loop()
    if getattr(_PASSWORD_HASH_LIMITER_STATE, "loop", None) is not loop:
        _PASSWORD_HASH_LIMITER_STATE.loop = loop
        _PASSWORD_HASH_LIMITER_STATE.semaphore = Semaphore(2)
    return _PASSWORD_HASH_LIMITER_STATE.semaphore


def hash_password(password: str) -> str:
    """新密码统一使用 Argon2id，不再生成新的 bcrypt 哈希。"""
    return password_hash.hash(password)


def verify_password_and_update(plain: str, hashed: str) -> tuple[bool, str | None]:
    """校验历史哈希；算法过旧时返回新哈希，由调用方在当前事务内保存。"""
    try:
        return password_hash.verify_and_update(plain, hashed)
    except (UnknownHashError, Argon2InvalidHashError, ValueError, TypeError):
        # 数据库哈希格式异常时必须拒绝登录，不能因异常哈希导致 500。
        return False, None


def verify_password(plain: str, hashed: str) -> bool:
    verified, _ = verify_password_and_update(plain, hashed)
    return verified


async def hash_password_async(password: str) -> str:
    """在线程池中生成 Argon2id 哈希，避免阻塞 FastAPI 事件循环。"""
    async with _password_hash_limiter():
        return await run_in_threadpool(hash_password, password)


async def verify_password_and_update_async(
    plain: str, hashed: str
) -> tuple[bool, str | None]:
    """在线程池中校验/升级哈希，并限制每进程 Argon2 并发。"""
    async with _password_hash_limiter():
        return await run_in_threadpool(verify_password_and_update, plain, hashed)


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


def _jwt_sign_key(settings) -> str:
    algo = (settings.jwt_algorithm or "HS256").upper()
    if algo.startswith("RS") or algo.startswith("ES") or algo in ("EDDSA",):
        key = (settings.jwt_private_key or "").strip()
        if not key:
            raise ValueError(f"JWT_ALGORITHM={algo} 需要配置 JWT_PRIVATE_KEY")
        return key
    return settings.jwt_secret


def _jwt_verify_key(settings) -> str:
    algo = (settings.jwt_algorithm or "HS256").upper()
    if algo.startswith("RS") or algo.startswith("ES") or algo in ("EDDSA",):
        key = (settings.jwt_public_key or "").strip()
        if not key:
            raise ValueError(f"JWT_ALGORITHM={algo} 需要配置 JWT_PUBLIC_KEY")
        return key
    return settings.jwt_secret


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
    return jwt.encode(payload, _jwt_sign_key(settings), algorithm=settings.jwt_algorithm)


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
    return jwt.encode(payload, _jwt_sign_key(settings), algorithm=settings.jwt_algorithm)


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
    return jwt.encode(payload, _jwt_sign_key(settings), algorithm=settings.jwt_algorithm)


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
        _jwt_verify_key(settings),
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
        audience=settings.jwt_audience,
        options={
            "require": ["exp", "iat", "sub", "nbf", "aud"],
        },
        leeway=int(leeway_seconds or 0),
    )
