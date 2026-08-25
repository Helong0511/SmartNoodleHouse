from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.core.response import success
from app.core.security import create_access_token, verify_password
from app.models import User
from app.schemas.common import LoginRequest

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login")
async def login(data: LoginRequest, request: Request, db: DbSession):
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.username == data.username, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        return {"code": 401, "message": "用户名或密码错误", "data": None}
    token = create_access_token(user.id, {"role": user.role.code})
    return success(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role.code},
        },
        request=request,
    )


@router.get("/me")
async def me(request: Request, db: DbSession, authorization: str | None = None):
    from app.api.deps import get_current_user
    from fastapi import Header

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return {"code": 401, "message": "未登录", "data": None}
    from jose import JWTError
    from app.core.security import decode_token

    try:
        payload = decode_token(auth.split(" ", 1)[1])
        user_id = int(payload["sub"])
    except Exception:
        return {"code": 401, "message": "无效令牌", "data": None}
    result = await db.execute(select(User).options(selectinload(User.role)).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return {"code": 401, "message": "用户不存在", "data": None}
    return success({"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role.code}, request=request)
