from typing import Annotated, Optional, Tuple

from fastapi import Depends, Header, Request
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.error_codes import ACCOUNT_DISABLED, AUTH_FAILED, TOKEN_INVALID
from app.core.exceptions import AppException
from app.core.security import decode_customer_token, decode_token
from app.models import Customer, Permission, RolePermission, User

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppException(AUTH_FAILED, "未登录", status_code=401)
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("typ") != "staff":
            raise AppException(TOKEN_INVALID, "请使用员工账号登录", status_code=401)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise AppException(TOKEN_INVALID, "无效令牌", status_code=401)
    result = await db.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id, User.status == 1)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AppException(ACCOUNT_DISABLED, "用户不存在或已禁用", status_code=401)
    # JWT 撤销：token_version 不匹配则失效
    if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
        raise AppException(TOKEN_INVALID, "令牌已失效，请重新登录", status_code=401)
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
    # P2-11：顾客侧统一 AppException 业务码（HTTP status 保持 401，前端清 token 逻辑不变）
    if not authorization or not authorization.startswith("Bearer "):
        raise AppException(AUTH_FAILED, "请先登录", status_code=401)
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_customer_token(token)
        customer_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise AppException(TOKEN_INVALID, "无效顾客令牌", status_code=401)
    customer = await db.get(Customer, customer_id)
    if (
        not customer
        or customer.status != 1
        or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE"
    ):
        raise AppException(ACCOUNT_DISABLED, "顾客不存在或已禁用", status_code=401)
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        raise AppException(TOKEN_INVALID, "令牌已失效，请重新登录", status_code=401)
    request.state.customer = customer
    return customer


CurrentCustomer = Annotated[Customer, Depends(get_current_customer)]


async def get_optional_customer(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> Customer | None:
    """公开接口用顾客身份：无 token 或 token 无效时静默返回 None（走冷启动）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        payload = decode_customer_token(authorization.split(" ", 1)[1])
        customer_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    customer = await db.get(Customer, customer_id)
    if (
        not customer
        or customer.status != 1
        or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE"
    ):
        return None
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        return None
    return customer


OptionalCustomer = Annotated[Customer | None, Depends(get_optional_customer)]


async def get_staff_or_customer(
    db: DbSession,
    authorization: str | None = Header(default=None),
) -> Tuple[Optional[User], Optional[Customer]]:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppException(AUTH_FAILED, "未登录", status_code=401)
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("typ") == "customer":
            # P2-11：顾客分支统一 AppException 业务码
            customer = await db.get(Customer, int(payload["sub"]))
            if (
                not customer
                or customer.status != 1
                or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE"
            ):
                raise AppException(ACCOUNT_DISABLED, "顾客不存在或已禁用", status_code=401)
            if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
                raise AppException(TOKEN_INVALID, "令牌已失效，请重新登录", status_code=401)
            return None, customer
        # 仅接受员工令牌：拒绝 stream 摄像头短票据等其他类型混入
        if payload.get("typ") != "staff":
            raise AppException(TOKEN_INVALID, "无效令牌类型", status_code=401)
        user = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.id == int(payload["sub"]), User.status == 1)
            )
        ).scalar_one_or_none()
        if not user:
            raise AppException(ACCOUNT_DISABLED, "用户不存在", status_code=401)
        if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
            raise AppException(TOKEN_INVALID, "令牌已失效，请重新登录", status_code=401)
        return user, None
    except AppException:
        raise
    except (JWTError, KeyError, ValueError):
        raise AppException(TOKEN_INVALID, "无效令牌", status_code=401)


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
            raise AppException(40300, f"缺少权限: {permission}", status_code=403)
        return user

    return Depends(checker)


def require_kitchen():
    async def checker(user: CurrentUser, db: DbSession):
        perms = await get_user_permissions(db, user.role_id)
        if user.role.code in ("SUPER_ADMIN", "KITCHEN", "STORE_ADMIN") or "order:update" in perms:
            return user
        raise AppException(40300, "无权访问厨房系统", status_code=403)

    return Depends(checker)


def require_pos():
    """收银点餐：收银员 / 店长 / 有 order:update 权限的员工。"""

    async def checker(user: CurrentUser, db: DbSession):
        perms = await get_user_permissions(db, user.role_id)
        if user.role.code in ("SUPER_ADMIN", "STORE_ADMIN", "CASHIER") or "order:update" in perms:
            return user
        raise AppException(40300, "无权使用收银点餐", status_code=403)

    return Depends(checker)


def require_settlement_manage(detail: str = "仅店长或超管可执行该操作"):
    """日结与退款审批：店长、超管，或持有 payment:refund 的角色。"""

    async def checker(user: CurrentUser, db: DbSession):
        if user.role.code in ("SUPER_ADMIN", "STORE_ADMIN"):
            return user
        perms = await get_user_permissions(db, user.role_id)
        if "payment:refund" not in perms:
            raise AppException(40300, detail, status_code=403)
        return user

    return Depends(checker)
