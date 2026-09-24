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
from app.models import Permission, Role, RolePermission, Store, User
from app.services.audit_service import AuditService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/users", tags=["员工"])

# 门店管理员不可分配的角色
STORE_ADMIN_FORBIDDEN_ROLES = {"SUPER_ADMIN"}

# 权限矩阵模块展示名（未列出的 module 原样回退）
MODULE_LABELS: dict[str, str] = {
    "product": "商品",
    "order": "订单",
    "customer": "顾客",
    "camera": "摄像头",
    "welcome": "迎宾",
    "payment": "支付",
    "table": "桌台",
    "user": "员工",
    "system": "系统",
    "coupon": "优惠券",
    "audit": "审计",
    "wallet": "储值",
}

# 超管角色权限固定为全部，后台不可改
LOCKED_ROLE_CODES = {"SUPER_ADMIN"}


class RolePermissionsUpdate(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)


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


@router.get("/role-permissions-matrix", dependencies=[require_permission("system:manage")])
async def role_permissions_matrix(request: Request, db: DbSession, user: CurrentUser):
    """角色×权限矩阵（仅超管/持 system:manage 可读）。"""
    roles = list((await db.execute(select(Role).order_by(Role.id))).scalars().all())
    perms = list(
        (await db.execute(select(Permission).order_by(Permission.module, Permission.id))).scalars().all()
    )
    rp_rows = list((await db.execute(select(RolePermission))).scalars().all())
    granted: dict[int, set[str]] = {}
    code_by_id = {p.id: p.code for p in perms}
    for rp in rp_rows:
        code = code_by_id.get(rp.permission_id)
        if not code:
            continue
        granted.setdefault(rp.role_id, set()).add(code)

    modules_map: dict[str, list[dict]] = {}
    for p in perms:
        modules_map.setdefault(p.module, []).append(
            {"id": p.id, "code": p.code, "name": p.name}
        )
    modules = [
        {
            "module": mod,
            "label": MODULE_LABELS.get(mod, mod),
            "permissions": items,
        }
        for mod, items in modules_map.items()
    ]

    role_payload = []
    for r in roles:
        codes = sorted(granted.get(r.id, set()))
        # 超管展示上视为拥有全部权限点
        if r.code in LOCKED_ROLE_CODES:
            codes = sorted(code_by_id.values())
        role_payload.append(
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "description": r.description,
                "editable": r.code not in LOCKED_ROLE_CODES,
                "permission_codes": codes,
            }
        )

    return success(
        {
            "roles": role_payload,
            "modules": modules,
            "hints": [
                "超级管理员权限固定为全部，不可在此修改。",
                "保存后该角色下员工需重新登录或刷新「我的信息」后菜单与接口权限才完全同步。",
                "退款权限（payment:refund）建议仅授予店长及以上。",
                "门店业务设置（settings:store）在「门店 → 门店信息 → 业务参数」只改本店；平台级安全与小程序主体仍要 system:manage。",
            ],
        },
        request=request,
    )


@router.put("/roles/{role_id}/permissions", dependencies=[require_permission("system:manage")])
async def update_role_permissions(
    role_id: int,
    data: RolePermissionsUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """覆盖保存某角色的权限集合（不可改 SUPER_ADMIN）。"""
    role = await _get_role(db, role_id)
    if role.code in LOCKED_ROLE_CODES:
        raise AppException(40314, "超级管理员权限不可修改")

    wanted = {c.strip() for c in (data.permission_codes or []) if c and c.strip()}
    # 禁止给非超管角色授予 system:manage，避免越权创建超管
    if "system:manage" in wanted and role.code != "SUPER_ADMIN":
        raise AppException(40315, "仅超级管理员可拥有 system:manage")

    all_perms = list((await db.execute(select(Permission))).scalars().all())
    by_code = {p.code: p for p in all_perms}
    unknown = sorted(wanted - set(by_code.keys()))
    if unknown:
        raise AppException(40084, f"未知权限码：{', '.join(unknown)}")

    old_codes = set(
        (
            await db.execute(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            )
        )
        .scalars()
        .all()
    )
    new_codes = set(wanted)

    # 全量替换
    existing_rps = list(
        (await db.execute(select(RolePermission).where(RolePermission.role_id == role.id))).scalars().all()
    )
    for rp in existing_rps:
        await db.delete(rp)
    await db.flush()
    for code in sorted(new_codes):
        db.add(RolePermission(role_id=role.id, permission_id=by_code[code].id))

    # 吊销该角色员工旧会话，促使其重新拉权限
    affected = list(
        (await db.execute(select(User).where(User.role_id == role.id))).scalars().all()
    )
    for u in affected:
        u.token_version = int(getattr(u, "token_version", 0) or 0) + 1

    await AuditService.log(
        db,
        user_id=user.id,
        action="role.permissions.update",
        module="user",
        target_type="role",
        target_id=role.id,
        detail={
            "role_code": role.code,
            "added": sorted(new_codes - old_codes),
            "removed": sorted(old_codes - new_codes),
            "permission_codes": sorted(new_codes),
            "affected_users": len(affected),
        },
        request=request,
    )
    await db.flush()
    return success(
        {
            "id": role.id,
            "code": role.code,
            "name": role.name,
            "permission_codes": sorted(new_codes),
            "affected_users": len(affected),
        },
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
