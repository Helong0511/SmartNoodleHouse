from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
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
    token = create_access_token(
        user.id,
        {"role": user.role.code, "store_id": user.store_id},
        token_version=getattr(user, "token_version", 0) or 0,
    )
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
            },
        },
        request=request,
    )


@router.get("/me")
async def me(request: Request, db: DbSession, user: CurrentUser):
    return success(
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role.code,
            "store_id": user.store_id,
        },
        request=request,
    )
