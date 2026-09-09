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


class VerifyPasswordRequest(BaseModel):
    password: str = Field(min_length=1)


@router.post("/login")
async def login(data: LoginRequest, request: Request, db: DbSession):
    from app.api.deps import get_user_permissions
    from app.services.audit_service import AuditService

    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.username == data.username, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        if user:
            await AuditService.log_login(
                db, user_id=user.id, username=data.username, success=False, request=request
            )
            await db.flush()
        else:
            await AuditService.log(
                db,
                action="auth.login_failed",
                module="auth",
                detail={"username": data.username, "success": False},
                request=request,
            )
            await db.flush()
        return {"code": 401, "message": "用户名或密码错误", "data": None}
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
    row = await db.get(User, user.id)
    if not row or not verify_password(data.password, row.password_hash):
        return {"code": 401, "message": "密码错误", "data": None}
    return success({"ok": True}, request=request)
