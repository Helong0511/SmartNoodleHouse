"""员工账号管理（多店绑店）"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.core.security import hash_password
from app.models import Role, Store, User
from app.services.audit_service import AuditService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/users", tags=["员工"])

# 门店管理员不可分配的角色
STORE_ADMIN_FORBIDDEN_ROLES = {"SUPER_ADMIN"}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=100)
    role_id: int
    store_id: int | None = None
    status: int = 1


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role_id: int | None = None
    store_id: int | None = None
    status: int | None = None
    clear_store: bool = False


class ResetPassword(BaseModel):
    password: str = Field(..., min_length=6, max_length=64)


def _ser(u: User, stores: dict[int, str] | None = None) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role_id": u.role_id,
        "role_code": u.role.code if u.role else None,
        "role_name": u.role.name if u.role else None,
        "store_id": u.store_id,
        "store_name": (stores or {}).get(u.store_id) if u.store_id else None,
        "status": u.status,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if getattr(u, "created_at", None) else None,
    }


async def _get_role(db, role_id: int) -> Role:
    role = await db.get(Role, role_id)
    if not role:
        raise AppException(40080, "角色不存在")
    return role


async def _load_user_with_role(db, user_id: int) -> User:
    u = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user_id))
    ).scalar_one()
    return u


def _assert_can_assign_role(actor: User, role: Role) -> None:
    if actor.role.code == "SUPER_ADMIN":
        return
    if role.code in STORE_ADMIN_FORBIDDEN_ROLES:
        raise AppException(40310, "无权分配该角色")
    if role.code == "STORE_ADMIN" and actor.role.code != "SUPER_ADMIN":
        # 门店管理员可以建收银/厨房，一般不建其他店长；允许本店再建 STORE_ADMIN 也可
        pass


@router.get("/roles", dependencies=[require_permission("user:read")])
async def list_roles(request: Request, db: DbSession, user: CurrentUser):
    rows = list((await db.execute(select(Role).order_by(Role.id))).scalars().all())
    if user.role.code != "SUPER_ADMIN":
        rows = [r for r in rows if r.code not in STORE_ADMIN_FORBIDDEN_ROLES]
    return success(
        [{"id": r.id, "code": r.code, "name": r.name, "description": r.description} for r in rows],
        request=request,
    )


@router.get("", dependencies=[require_permission("user:read")])
async def list_users(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    store_id: int | None = None,
    status: int | None = None,
    role_id: int | None = None,
):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    effective = require_bound_store(user, store_id)
    q = select(User).options(selectinload(User.role))
    if effective is not None:
        q = q.where(User.store_id == effective)
    if status is not None:
        q = q.where(User.status == status)
    if role_id:
        q = q.where(User.role_id == int(role_id))
    if keyword:
        kw = f"%{keyword.strip()}%"
        q = q.where(or_(User.username.like(kw), User.display_name.like(kw)))
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    store_ids = [u.store_id for u in rows if u.store_id]
    store_names: dict[int, str] = {}
    if store_ids:
        for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars():
            store_names[s.id] = s.name
    return success(
        {
            "items": [_ser(u, store_names) for u in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.post("", dependencies=[require_permission("user:manage")])
async def create_user(data: UserCreate, request: Request, db: DbSession, user: CurrentUser):
    username = data.username.strip()
    exists = await db.scalar(select(User.id).where(User.username == username).limit(1))
    if exists:
        raise AppException(40081, f"用户名「{username}」已存在")

    role = await _get_role(db, data.role_id)
    _assert_can_assign_role(user, role)

    if user.role.code == "SUPER_ADMIN":
        sid = int(data.store_id) if data.store_id else None
        if role.code != "SUPER_ADMIN" and not sid:
            raise AppException(40082, "非超管账号必须绑定门店")
    else:
        sid = require_bound_store(user, data.store_id)
        if sid is None:
            raise AppException(40082, "账号未绑定门店")
        assert_store_access(user, sid)

    if sid:
        store = await db.get(Store, sid)
        if not store or store.status != 1:
            raise AppException(40061, "门店不存在或已停用")

    u = User(
        username=username,
        password_hash=hash_password(data.password),
        display_name=data.display_name.strip(),
        role_id=role.id,
        store_id=sid,
        status=1 if data.status is None else int(data.status),
    )
    db.add(u)
    await db.flush()
    u = await _load_user_with_role(db, u.id)
    store_names: dict[int, str] = {}
    if sid:
        store = await db.get(Store, sid)
        if store:
            store_names[sid] = store.name
    await AuditService.log(
        db,
        user_id=user.id,
        action="user.create",
        module="user",
        target_type="user",
        target_id=u.id,
        detail={
            "username": u.username,
            "display_name": u.display_name,
            "role_id": role.id,
            "role_code": role.code,
            "store_id": sid,
        },
        request=request,
    )
    return success(_ser(u, store_names), request=request)


@router.put("/{user_id}", dependencies=[require_permission("user:manage")])
async def update_user(
    user_id: int,
    data: UserUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    target = (
        await db.execute(select(User).options(selectinload(User.role)).where(User.id == user_id))
    ).scalar_one_or_none()
    if not target:
        raise AppException(40480, "员工不存在")
    if target.store_id:
        assert_store_access(user, target.store_id)
    elif user.role.code != "SUPER_ADMIN":
        raise AppException(40311, "无权管理该员工")

    if data.display_name is not None:
        target.display_name = data.display_name.strip()
    perm_changes: dict = {}
    if data.status is not None:
        if target.id == user.id and int(data.status) != 1:
            raise AppException(40083, "不能禁用自己的账号")
        new_status = int(data.status)
        if new_status != int(target.status):
            perm_changes["status"] = {"from": int(target.status), "to": new_status}
            target.status = new_status
            if new_status != 1:
                target.token_version = int(getattr(target, "token_version", 0) or 0) + 1
    if data.role_id is not None:
        role = await _get_role(db, data.role_id)
        _assert_can_assign_role(user, role)
        if role.id != target.role_id:
            perm_changes["role_id"] = {"from": target.role_id, "to": role.id, "role_code": role.code}
            target.role_id = role.id
            target.token_version = int(getattr(target, "token_version", 0) or 0) + 1
    if data.clear_store:
        if user.role.code != "SUPER_ADMIN":
            raise AppException(40312, "仅超管可清空门店绑定")
        if target.store_id is not None:
            perm_changes["store_id"] = {"from": target.store_id, "to": None}
        target.store_id = None
    elif data.store_id is not None:
        assert_store_access(user, data.store_id)
        if user.role.code != "SUPER_ADMIN":
            # 门店账号不能把员工改到别的店
            sid = require_bound_store(user)
            if sid != int(data.store_id):
                raise AppException(40313, "不能将员工改绑到其他门店")
        if target.store_id != int(data.store_id):
            perm_changes["store_id"] = {"from": target.store_id, "to": int(data.store_id)}
        target.store_id = int(data.store_id)

    if data.display_name is not None:
        perm_changes["display_name"] = data.display_name.strip()

    if perm_changes:
        await AuditService.log(
            db,
            user_id=user.id,
            action="user.update",
            module="user",
            target_type="user",
            target_id=target.id,
            detail=perm_changes,
            request=request,
        )

    await db.flush()
    target = await _load_user_with_role(db, target.id)
    store_names = {}
    if target.store_id:
        s = await db.get(Store, target.store_id)
        if s:
            store_names[s.id] = s.name
    return success(_ser(target, store_names), request=request)


@router.post("/{user_id}/reset-password", dependencies=[require_permission("user:manage")])
async def reset_password(
    user_id: int,
    data: ResetPassword,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    target = await db.get(User, user_id)
    if not target:
        raise AppException(40480, "员工不存在")
    if target.store_id:
        assert_store_access(user, target.store_id)
    elif user.role.code != "SUPER_ADMIN":
        raise AppException(40311, "无权管理该员工")
    target.password_hash = hash_password(data.password)
    target.token_version = int(target.token_version or 0) + 1
    await db.flush()
    await AuditService.log(
        db,
        user_id=user.id,
        action="user.reset_password",
        module="user",
        target_type="user",
        target_id=target.id,
        detail={"username": target.username},
        request=request,
    )
    return success({"id": target.id, "token_version": target.token_version}, request=request)
