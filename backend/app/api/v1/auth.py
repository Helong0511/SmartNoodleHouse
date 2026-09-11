from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.core.response import now_local, success
from app.core.security import create_access_token, verify_password
from app.models import User
from app.schemas.common import LoginRequest
from pydantic import BaseModel, Field

router = APIRouter(prefix="/auth", tags=["认证"])

# 登录防爆破：按「用户名+客户端 IP」累计，避免跨 IP 锁死合法账号
_LOGIN_FAIL_LIMIT = 8
_LOGIN_LOCK_SECONDS = 15 * 60
_LOGIN_FAIL_WINDOW = 15 * 60


class VerifyPasswordRequest(BaseModel):
    password: str = Field(min_length=1)


def _client_ip(request: Request | None) -> str:
    if request is None:
        return "unknown"
    try:
        from app.core.ip_allowlist import client_ip

        return (client_ip(request) or "").strip() or "unknown"
    except Exception:
        peer = request.client.host if request.client else ""
        return (peer or "unknown").strip() or "unknown"


def _login_scope(username: str, ip: str) -> str:
    u = (username or "").strip().lower()
    return f"{u}|{(ip or 'unknown').strip()}"


def _login_fail_key(username: str, ip: str) -> str:
    return f"snh:login:fail:{_login_scope(username, ip)}"


def _login_lock_key(username: str, ip: str) -> str:
    return f"snh:login:lock:{_login_scope(username, ip)}"


async def _login_is_locked(username: str, ip: str = "unknown") -> bool:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        return bool(await r.get(_login_lock_key(username, ip)))
    except Exception:
        # Redis 不可用：不误锁全员；依赖 RateLimitMiddleware 兜底
        return False


async def _record_login_failure(username: str, ip: str = "unknown") -> None:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        fail_key = _login_fail_key(username, ip)
        count = await r.incr(fail_key)
        if count == 1:
            await r.expire(fail_key, _LOGIN_FAIL_WINDOW)
        if int(count) >= _LOGIN_FAIL_LIMIT:
            await r.set(_login_lock_key(username, ip), "1", ex=_LOGIN_LOCK_SECONDS)
            await r.delete(fail_key)
    except Exception:
        pass


async def _clear_login_failures(username: str, ip: str = "unknown") -> None:
    try:
        from app.core.redis_client import get_redis

        r = await get_redis()
        await r.delete(_login_fail_key(username, ip), _login_lock_key(username, ip))
    except Exception:
        pass


@router.post("/login")
async def login(data: LoginRequest, request: Request, db: DbSession):
    from app.api.deps import get_user_permissions
    from app.services.audit_service import AuditService

    username = (data.username or "").strip()
    ip = _client_ip(request)
    if await _login_is_locked(username, ip):
        return {
            "code": 429,
            "message": "登录失败次数过多，请 15 分钟后再试",
            "data": None,
        }

    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.username == username, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        await _record_login_failure(username, ip)
        if user:
            await AuditService.log_login(
                db, user_id=user.id, username=username, success=False, request=request
            )
            await db.flush()
        else:
            await AuditService.log(
                db,
                action="auth.login_failed",
                module="auth",
                detail={"username": username, "success": False},
                request=request,
            )
            await db.flush()
        return {"code": 401, "message": "用户名或密码错误", "data": None}

    await _clear_login_failures(username, ip)
    user.last_login_at = now_local()
    await AuditService.log_login(
        db, user_id=user.id, username=user.username, success=True, request=request
    )
    await db.flush()
    token = create_access_token(
        user.id,
        {"role": user.role.code, "store_id": user.store_id},
        token_version=getattr(user, "token_version", 0) or 0,
    )
    perms = sorted(await get_user_permissions(db, user.role_id))
    if user.role.code == "SUPER_ADMIN":
        perms = sorted(set(perms) | {"system:manage"})
    return success(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role.code,
                "store_id": user.store_id,
                "permissions": perms,
            },
        },
        request=request,
    )


@router.get("/me")
async def me(request: Request, db: DbSession, user: CurrentUser):
    from app.api.deps import get_user_permissions

    perms = sorted(await get_user_permissions(db, user.role_id))
    if user.role.code == "SUPER_ADMIN" and "system:manage" not in perms:
        # SUPER_ADMIN 可绕过权限检查；前端菜单仍按码判断
        perms = sorted(set(perms) | {"system:manage"})
    return success(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role.code,
            "store_id": user.store_id,
            "permissions": perms,
        },
        request=request,
    )


@router.post("/verify-password")
async def verify_password_api(data: VerifyPasswordRequest, request: Request, db: DbSession, user: CurrentUser):
    """锁屏解锁：校验当前登录用户密码。"""
    lock_name = f"uid:{user.id}"
    if await _login_is_locked(lock_name):
        return {
            "code": 429,
            "message": "密码错误次数过多，请 15 分钟后再试",
            "data": None,
        }
    row = await db.get(User, user.id)
    if not row or not verify_password(data.password, row.password_hash):
        await _record_login_failure(lock_name)
        return {"code": 401, "message": "密码错误", "data": None}
    await _clear_login_failures(lock_name)
    return success({"ok": True}, request=request)
