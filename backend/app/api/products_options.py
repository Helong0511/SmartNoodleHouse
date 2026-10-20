"""规格组 / 规格项 CRUD：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, DbSession, OptionalStaffUser, require_permission
from app.api.products_common import store_code_prefix
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Option, OptionGroup
from app.schemas.product import OptionCreate, OptionGroupCreate, OptionGroupUpdate, OptionUpdate
from app.services.audit_service import AuditService
from app.services.store_scope import require_bound_store

router = APIRouter(tags=["餐品"])

def _option_dict(o: Option) -> dict:
    return {
        "id": o.id,
        "group_id": o.group_id,
        "name": o.name,
        "code": o.code,
        "extra_price": str(o.extra_price),
        "sort_order": o.sort_order,
        "is_default": bool(o.is_default),
        "status": o.status,
    }


async def _clear_sibling_defaults(db: DbSession, group_id: int, keep_id: int) -> None:
    """同规格组仅保留一个默认项（单选组）。"""
    group = await db.get(OptionGroup, group_id)
    if not group or group.selection_type != "SINGLE":
        return
    result = await db.execute(
        select(Option).where(Option.group_id == group_id, Option.id != keep_id, Option.is_default.is_(True))
    )
    for sibling in result.scalars():
        sibling.is_default = False
    await db.flush()


async def _apply_option_default_flag(db: DbSession, opt: Option, want_default: bool | None) -> None:
    if want_default is True:
        opt.is_default = True
        await db.flush()
        await _clear_sibling_defaults(db, opt.group_id, opt.id)
    elif want_default is False:
        opt.is_default = False
        await db.flush()


def assert_option_group_manage(user: CurrentUser, group: OptionGroup | None = None) -> None:
    """门店仅可改本店规格组；总部共享仅超管可改。未绑定门店的非超管一律拒绝。"""
    role = getattr(getattr(user, "role", None), "code", None) or ""
    if role == "SUPER_ADMIN":
        return
    if not user.store_id:
        raise AppException(40301, "账号未绑定门店，无法管理规格组")
    if group is None:
        return
    if group.store_id is None:
        raise AppException(40301, "无权修改总部共享规格组")
    if int(group.store_id) != int(user.store_id):
        raise AppException(40301, "无权修改其他门店规格组")


async def _resolve_create_group_store_id(user: CurrentUser, requested: int | None) -> int | None:
    role = getattr(getattr(user, "role", None), "code", None) or ""
    if role == "SUPER_ADMIN":
        return int(requested) if requested else None
    bound = require_bound_store(user, None)
    if bound is None:
        raise AppException(40301, "账号未绑定门店，无法创建规格组")
    return int(bound)


async def _ensure_unique_group_code(db: DbSession, code: str, store_id: int | None, *, exclude_id: int | None = None) -> None:
    q = select(OptionGroup.id).where(OptionGroup.code == code)
    if store_id is None:
        q = q.where(OptionGroup.store_id.is_(None))
    else:
        q = q.where(OptionGroup.store_id == int(store_id))
    if exclude_id is not None:
        q = q.where(OptionGroup.id != int(exclude_id))
    exists = await db.scalar(q.limit(1))
    if exists:
        scope = "总部" if store_id is None else f"门店#{store_id}"
        raise AppException(40003, f"{scope}已存在编码「{code}」的规格组")


async def _normalize_group_code(db: DbSession, raw: str, store_id: int | None) -> str:
    from app.services.option_code import MAX_GROUP_CODE, format_group_code, validate_prefixed_code

    prefix = await store_code_prefix(db, store_id)
    try:
        code = format_group_code(prefix, raw)
        return validate_prefixed_code(code, max_len=MAX_GROUP_CODE, expect_prefix=prefix)
    except ValueError as e:
        raise AppException(40001, str(e)) from e


async def _normalize_option_code(db: DbSession, raw: str, group: OptionGroup) -> str:
    from app.services.option_code import MAX_OPTION_CODE, format_option_code, validate_prefixed_code

    prefix = await store_code_prefix(db, group.store_id)
    try:
        code = format_option_code(prefix, group.code, raw)
        return validate_prefixed_code(code, max_len=MAX_OPTION_CODE, expect_prefix=prefix)
    except ValueError as e:
        raise AppException(40001, str(e)) from e


@router.get("/options/groups")
async def list_option_groups(
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    include_disabled: bool = False,
    store_id: int | None = None,
):
    """规格组列表：门店见「总部共享 ∪ 本店」；超管可按 store_id 过滤。"""
    if include_disabled:
        if staff is None:
            raise AppException(40101, "查看停用规格须登录员工账号")
        from app.api.deps import get_user_permissions

        perms = await get_user_permissions(db, staff.role_id)
        if staff.role.code != "SUPER_ADMIN" and "product:read" not in perms:
            raise AppException(40301, "缺少权限: product:read")
    q = select(OptionGroup).order_by(OptionGroup.sort_order, OptionGroup.id)
    if not include_disabled:
        q = q.where(OptionGroup.status == 1)

    scope_store: int | None = None
    if staff is not None:
        if staff.role.code == "SUPER_ADMIN":
            scope_store = int(store_id) if store_id else None
        else:
            if not staff.store_id:
                raise AppException(40301, "账号未绑定门店", status_code=403)
            scope_store = int(staff.store_id)
    elif store_id:
        scope_store = int(store_id)
    else:
        raise AppException(40001, "请指定门店 store_id")

    if scope_store is not None:
        q = q.where(or_(OptionGroup.store_id.is_(None), OptionGroup.store_id == scope_store))

    groups_rows = list((await db.execute(q)).scalars().all())
    if not groups_rows:
        return success([], request=request)

    gids = [g.id for g in groups_rows]
    opts_q = select(Option).where(Option.group_id.in_(gids)).order_by(Option.sort_order, Option.id)
    if not include_disabled:
        opts_q = opts_q.where(Option.status == 1)
    opts_by_group: dict[int, list] = {gid: [] for gid in gids}
    for o in (await db.execute(opts_q)).scalars().all():
        opts_by_group.setdefault(int(o.group_id), []).append(o)

    groups = [
        {
            "id": g.id,
            "store_id": g.store_id,
            "name": g.name,
            "code": g.code,
            "selection_type": g.selection_type,
            "is_required": g.is_required,
            "sort_order": g.sort_order,
            "status": g.status,
            "options": [_option_dict(o) for o in opts_by_group.get(g.id, [])],
        }
        for g in groups_rows
    ]
    return success(groups, request=request)


@router.post("/options/groups", dependencies=[require_permission("product:create")])
async def create_option_group(data: OptionGroupCreate, request: Request, db: DbSession, user: CurrentUser):
    store_id = await _resolve_create_group_store_id(user, data.store_id)
    code = await _normalize_group_code(db, data.code or "", store_id)
    await _ensure_unique_group_code(db, code, store_id)
    payload = data.model_dump()
    payload["code"] = code
    payload["store_id"] = store_id
    group = OptionGroup(**payload)
    db.add(group)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option_group.create",
        module="product",
        target_type="option_group",
        target_id=int(group.id),
        detail={"store_id": int(group.store_id) if group.store_id else None, "code": group.code, "name": data.name},
        request=request,
        store_id=int(group.store_id) if group.store_id else None,
    )
    return success({"id": group.id, "store_id": group.store_id, "code": group.code}, request=request)


@router.post("/options", dependencies=[require_permission("product:create")])
async def create_option(data: OptionCreate, request: Request, db: DbSession, user: CurrentUser):
    group = await db.get(OptionGroup, data.group_id)
    if not group:
        raise AppException(40003, "规格组不存在")
    assert_option_group_manage(user, group)
    payload = data.model_dump()
    want_default = payload.pop("is_default", False)
    payload["code"] = await _normalize_option_code(db, str(payload.get("code") or ""), group)
    # 组内 code 唯一
    exists = await db.scalar(
        select(Option.id).where(Option.group_id == group.id, Option.code == payload["code"]).limit(1)
    )
    if exists:
        raise AppException(40003, f"该规格组已存在编码「{payload['code']}」")
    opt = Option(**payload, is_default=bool(want_default))
    db.add(opt)
    await db.flush()
    if opt.is_default:
        await _clear_sibling_defaults(db, opt.group_id, opt.id)
    await db.refresh(opt)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option.create",
        module="product",
        target_type="option",
        target_id=int(opt.id),
        detail={"group_id": int(opt.group_id), "code": opt.code, "name": opt.name, "is_default": bool(opt.is_default)},
        request=request,
    )
    return success(_option_dict(opt), request=request)


@router.put("/options/groups/{group_id}", dependencies=[require_permission("product:update")])
async def update_option_group(
    group_id: int, data: OptionGroupUpdate, request: Request, db: DbSession, user: CurrentUser
):
    group = await db.get(OptionGroup, group_id)
    if not group:
        return success(None, message="规格组不存在", request=request)
    assert_option_group_manage(user, group)
    raw = data.model_dump(exclude_unset=True)
    role = getattr(getattr(user, "role", None), "code", None) or ""
    if role != "SUPER_ADMIN":
        raw.pop("store_id", None)
    target_sid = raw.get("store_id", group.store_id) if "store_id" in raw else group.store_id
    if "code" in raw and raw["code"] is not None:
        new_code = await _normalize_group_code(db, str(raw["code"]), target_sid)
        await _ensure_unique_group_code(db, new_code, target_sid, exclude_id=group.id)
        raw["code"] = new_code
    elif "store_id" in raw:
        # 迁店时同步改前缀
        new_code = await _normalize_group_code(db, group.code, raw.get("store_id"))
        await _ensure_unique_group_code(db, new_code, raw.get("store_id"), exclude_id=group.id)
        raw["code"] = new_code
    for k, v in raw.items():
        setattr(group, k, v)
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option_group.update",
        module="product",
        target_type="option_group",
        target_id=int(group.id),
        detail={"store_id": int(group.store_id) if group.store_id else None, "code": group.code, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
        store_id=int(group.store_id) if group.store_id else None,
    )
    return success({"id": group.id, "store_id": group.store_id, "code": group.code}, request=request)


@router.delete("/options/groups/{group_id}", dependencies=[require_permission("product:delete")])
async def delete_option_group(group_id: int, request: Request, db: DbSession, user: CurrentUser):
    group = await db.get(OptionGroup, group_id)
    if not group:
        return success(None, message="规格组不存在", request=request)
    assert_option_group_manage(user, group)
    group.status = 0
    opts = await db.execute(select(Option).where(Option.group_id == group_id))
    for o in opts.scalars():
        o.status = 0
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option_group.delete",
        module="product",
        target_type="option_group",
        target_id=int(group.id),
        detail={"store_id": int(group.store_id) if group.store_id else None, "code": group.code, "name": group.name},
        request=request,
        store_id=int(group.store_id) if group.store_id else None,
    )
    return success({"id": group.id}, request=request)


@router.put("/options/{option_id}", dependencies=[require_permission("product:update")])
async def update_option(option_id: int, data: OptionUpdate, request: Request, db: DbSession, user: CurrentUser):
    opt = await db.get(Option, option_id)
    if not opt:
        return success(None, message="规格选项不存在", request=request)
    group = await db.get(OptionGroup, opt.group_id)
    assert_option_group_manage(user, group)
    fields = data.model_dump(exclude_unset=True)
    want_default = fields.pop("is_default", None)
    if "code" in fields and fields["code"] is not None:
        fields["code"] = await _normalize_option_code(db, str(fields["code"]), group)
        exists = await db.scalar(
            select(Option.id)
            .where(Option.group_id == group.id, Option.code == fields["code"], Option.id != opt.id)
            .limit(1)
        )
        if exists:
            raise AppException(40003, f"该规格组已存在编码「{fields['code']}」")
    for k, v in fields.items():
        setattr(opt, k, v)
    await db.flush()
    if want_default is not None:
        await _apply_option_default_flag(db, opt, want_default)
    await db.refresh(opt)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option.update",
        module="product",
        target_type="option",
        target_id=int(opt.id),
        detail={"group_id": int(opt.group_id), "code": opt.code, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
    )
    return success(_option_dict(opt), request=request)


@router.put("/options/{option_id}/default", dependencies=[require_permission("product:update")])
async def set_default_option(option_id: int, request: Request, db: DbSession, user: CurrentUser):
    """将单选规格组内某选项设为唯一默认（菜单 + 一键加购）。"""
    opt = await db.get(Option, option_id)
    if not opt:
        return success(None, message="规格选项不存在", request=request)
    group = await db.get(OptionGroup, opt.group_id)
    if not group:
        return success(None, message="规格组不存在", request=request)
    assert_option_group_manage(user, group)
    if group.selection_type != "SINGLE":
        raise AppException(40036, "仅单选规格组可设置默认项")
    if opt.status != 1:
        raise AppException(40037, "已停用的选项不能设为默认")
    await _apply_option_default_flag(db, opt, True)
    await db.refresh(opt)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option.set_default",
        module="product",
        target_type="option",
        target_id=int(opt.id),
        detail={"group_id": int(opt.group_id), "code": opt.code, "is_default": bool(opt.is_default)},
        request=request,
    )
    return success(_option_dict(opt), request=request)


@router.delete("/options/{option_id}", dependencies=[require_permission("product:delete")])
async def delete_option(option_id: int, request: Request, db: DbSession, user: CurrentUser):
    opt = await db.get(Option, option_id)
    if not opt:
        return success(None, message="规格选项不存在", request=request)
    group = await db.get(OptionGroup, opt.group_id)
    assert_option_group_manage(user, group)
    opt.status = 0
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="product.option.delete",
        module="product",
        target_type="option",
        target_id=int(opt.id),
        detail={"group_id": int(opt.group_id), "code": opt.code, "name": opt.name},
        request=request,
    )
    return success({"id": opt.id}, request=request)
