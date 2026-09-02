from typing import Annotated, Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import decode_customer_token, decode_token
from app.models import Customer, Permission, RolePermission, User

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("typ") != "staff":
            raise HTTPException(status_code=401, detail="请使用员工账号登录")
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="无效令牌")
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    # JWT 撤销：token_version 不匹配则失效
    if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_staff_user(
    request: Request,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User | None:
    """可选员工身份：无 token / 非员工令牌时返回 None（供公开商品列表等）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("typ") != "staff":
            return None
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user:
        return None
    if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
        return None
    request.state.user = user
    return user


OptionalStaffUser = Annotated[User | None, Depends(get_optional_staff_user)]


async def get_current_customer(
    request: Request,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> Customer:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_customer_token(token)
        customer_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="无效顾客令牌")
    customer = await db.get(Customer, customer_id)
    if not customer or customer.status != 1:
        raise HTTPException(status_code=401, detail="顾客不存在或已禁用")
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
    request.state.customer = customer
    return customer


CurrentCustomer = Annotated[Customer, Depends(get_current_customer)]


async def get_staff_or_customer(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> Tuple[Optional[User], Optional[Customer]]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("typ") == "customer":
            customer = await db.get(Customer, int(payload["sub"]))
            if not customer or customer.status != 1:
                raise HTTPException(status_code=401, detail="顾客不存在或已禁用")
            if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
                raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
            return None, customer
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.id == int(payload["sub"]), User.status == 1)
            )
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
            raise HTTPException(status_code=401, detail="令牌已失效，请重新登录")
        return user, None
    except HTTPException:
        raise
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="无效令牌")


StaffOrCustomer = Annotated[Tuple[Optional[User], Optional[Customer]], Depends(get_staff_or_customer)]


async def get_user_permissions(db: AsyncSession, role_id: int) -> set[str]:
    result = await db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return set(result.scalars().all())


def require_permission(permission: str):
    async def checker(user: CurrentUser, db: DbSession):
        perms = await get_user_permissions(db, user.role_id)
        if permission not in perms and user.role.code != "SUPER_ADMIN":
            raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
        return user

    return Depends(checker)


def require_kitchen():
    async def checker(user: CurrentUser, db: DbSession):
        perms = await get_user_permissions(db, user.role_id)
        if user.role.code in ("SUPER_ADMIN", "KITCHEN", "STORE_ADMIN") or "order:update" in perms:
            return user
        raise HTTPException(status_code=403, detail="无权访问厨房系统")

    return Depends(checker)


def require_pos():
    """收银点餐：收银员 / 店长 / 有 order:update 权限的员工。"""

    async def checker(user: CurrentUser, db: DbSession):
        perms = await get_user_permissions(db, user.role_id)
        if user.role.code in ("SUPER_ADMIN", "STORE_ADMIN", "CASHIER") or "order:update" in perms:
            return user
        raise HTTPException(status_code=403, detail="无权使用收银点餐")

    return Depends(checker)
