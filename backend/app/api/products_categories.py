"""分类 CRUD：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import delete, select

from app.api.deps import (
    CurrentUser,
    DbSession,
    OptionalStaffUser,
    require_permission,
)
from app.api.products_common import store_code_prefix
from app.core.exceptions import AppException
from app.core.response import success
from app.models import CategorySortScheduleItem, Product, ProductCategory, Store
from app.schemas.product import CategoryCreate, CategoryUpdate
from app.services.audit_service import AuditService
from app.services.category_require import normalize_require_category_ids
from app.services.category_schedule import apply_category_schedule_sort, parse_hhmm
from app.services.menu_cache_service import invalidate_store_menu_cache
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(tags=["餐品"])


async def _invalidate_category_menu(db: DbSession, *store_ids: int | None) -> None:
    """分类变更同时清理本地推荐/菜单缓存，并在业务事务内持久化代际与 Outbox。"""
    for sid in sorted({int(value) for value in store_ids if value is not None}):
        await invalidate_store_menu_cache(db, sid)


async def _prepare_category_store_move(
    db: DbSession, category: ProductCategory, target_store_id: int
) -> None:
    """迁移分类前阻止餐品跨门店悬挂，并清理旧门店的时段排序引用。"""
    target_sid = int(target_store_id)
    if int(category.store_id) == target_sid:
        return

    linked_product_id = await db.scalar(
        select(Product.id)
        .where(Product.category_id == int(category.id))
        .limit(1)
    )
    if linked_product_id is not None:
        raise AppException(
            40041,
            "该分类仍关联餐品，不能直接迁移到其他门店；请先在原门店重新分配或下架餐品",
        )

    # 分类依赖必须在目标门店仍然有效，否则迁移后会留下跨门店依赖。
    required_ids = normalize_require_category_ids(category.require_category_ids)
    if required_ids:
        await validate_require_category_ids(
            db, target_sid, required_ids, self_id=int(category.id)
        )

    # 分时段排序方案属于门店资源，迁移分类时删除旧门店的关联项。
    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.category_id == int(category.id)
        )
    )


def category_payload(
    cat: ProductCategory,
    store_name: str | None = None,
    *,
    schedule_id: int | None = None,
    schedule_name: str | None = None,
) -> dict:
    return {
        "id": cat.id,
        "store_id": cat.store_id,
        "store_name": store_name,
        "category_no": getattr(cat, "category_no", None) or "",
        "name": cat.name,
        "description": cat.description,
        "sort_order": cat.sort_order,
        "status": cat.status,
        "require_category_ids": normalize_require_category_ids(cat.require_category_ids),
        "schedule_id": schedule_id,
        "schedule_name": schedule_name,
    }


async def validate_require_category_ids(
    db: DbSession,
    store_id: int,
    require_ids: list[int] | None,
    *,
    self_id: int | None = None,
) -> list[int]:
    ids = normalize_require_category_ids(require_ids)
    if not ids:
        return []
    if self_id is not None and self_id in ids:
        raise AppException(40001, "分类不能依赖自身")
    rows = list(
        (
            await db.execute(
                select(ProductCategory).where(
                    ProductCategory.id.in_(ids),
                    ProductCategory.store_id == int(store_id),
                )
            )
        ).scalars()
    )
    found = {c.id for c in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise AppException(40001, f"依赖分类不存在或不属于本店: {missing}")
    return ids


@router.get("/categories")
async def list_categories(
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    status: int | None = 1,
    all: bool = False,
    store_id: int | None = None,
    at: str | None = None,
    apply_schedule: bool = True,
):
    """分类列表。小程序/点餐须传 store_id；后台 all=true 需员工登录。

    有 store_id 且非 all 时，按当前（或 at=HH:MM）时段方案重排 sort_order。
    """
    if store_id is None and not (staff is not None and all):
        raise AppException(40001, "请指定门店 store_id")
    if all and staff is None:
        raise AppException(40101, "查看全部分类须登录员工账号")
    if staff is not None and store_id is not None:
        store_id = require_bound_store(staff, store_id)
    elif staff is not None and all and store_id is None and staff.role.code != "SUPER_ADMIN":
        store_id = require_bound_store(staff, None)

    q = select(ProductCategory).order_by(ProductCategory.sort_order, ProductCategory.id)
    if not all and status is not None:
        q = q.where(ProductCategory.status == status)
    if store_id is not None:
        q = q.where(ProductCategory.store_id == int(store_id))
    result = await db.execute(q)
    cats = list(result.scalars().all())
    active = None
    at_t = None
    if at:
        try:
            at_t = parse_hhmm(at)
        except ValueError as exc:
            raise AppException(40001, str(exc)) from exc
    if apply_schedule and store_id is not None and not all:
        cats, active = await apply_category_schedule_sort(
            db, cats, store_id=int(store_id), at=at_t
        )
    store_ids = list({c.store_id for c in cats if c.store_id})
    store_map: dict[int, str] = {}
    if store_ids:
        store_map = {
            s.id: s.name
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    payloads = []
    for i, c in enumerate(cats):
        row = category_payload(
            c,
            store_map.get(c.store_id),
            schedule_id=int(active.id) if active else None,
            schedule_name=active.name if active else None,
        )
        if active is not None:
            row["sort_order"] = i
            row["default_sort_order"] = int(c.sort_order or 0)
        payloads.append(row)
    return success(payloads, request=request)


@router.post("/categories", dependencies=[require_permission("product:create")])
async def create_category(data: CategoryCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.option_code import (
        MAX_CATEGORY_NO,
        format_category_no,
        name_to_logical_code,
        validate_prefixed_code,
    )

    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建分类")
    assert_store_access(user, sid)
    prefix = await store_code_prefix(db, int(sid))
    raw_no = (data.category_no or "").strip() or name_to_logical_code(data.name)
    try:
        category_no = format_category_no(prefix, raw_no)
        validate_prefixed_code(category_no, max_len=MAX_CATEGORY_NO, expect_prefix=prefix)
    except ValueError as e:
        raise AppException(40001, str(e)) from e
    exists = await db.scalar(
        select(ProductCategory.id).where(
            ProductCategory.store_id == int(sid), ProductCategory.category_no == category_no
        ).limit(1)
    )
    if exists:
        raise AppException(40002, f"本店分类编号「{category_no}」已存在")
    payload = data.model_dump(exclude={"store_id", "require_category_ids", "category_no"})
    require_ids = await validate_require_category_ids(db, int(sid), data.require_category_ids)
    cat = ProductCategory(
        store_id=int(sid),
        category_no=category_no,
        require_category_ids=require_ids or None,
        **payload,
    )
    db.add(cat)
    await db.flush()
    await _invalidate_category_menu(db, int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category.create",
        module="product",
        target_type="category",
        target_id=int(cat.id),
        detail={"store_id": int(sid), "category_no": cat.category_no, "name": data.name},
        request=request,
        store_id=int(sid),
    )
    return success({"id": cat.id, "store_id": cat.store_id, "category_no": cat.category_no}, request=request)


@router.put("/categories/{cat_id}", dependencies=[require_permission("product:update")])
async def update_category(cat_id: int, data: CategoryUpdate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.option_code import MAX_CATEGORY_NO, format_category_no, validate_prefixed_code

    cat = await db.get(ProductCategory, cat_id)
    if not cat:
        return success(None, message="分类不存在", request=request)
    assert_store_access(user, cat.store_id)
    original_store_id = int(cat.store_id)
    payload = data.model_dump(exclude_unset=True)
    if "store_id" in payload and payload["store_id"] is not None:
        if user.role.code != "SUPER_ADMIN":
            raise AppException(40301, "仅超管可迁移分类所属门店")
        assert_store_access(user, int(payload["store_id"]))
        await _prepare_category_store_move(db, cat, int(payload["store_id"]))
        cat.store_id = int(payload["store_id"])
        del payload["store_id"]
        # 迁店时同步编号前缀
        prefix = await store_code_prefix(db, int(cat.store_id))
        cat.category_no = format_category_no(prefix, cat.category_no or cat.name)
    if "category_no" in payload and payload["category_no"] is not None:
        prefix = await store_code_prefix(db, int(cat.store_id))
        try:
            new_no = format_category_no(prefix, str(payload.pop("category_no")))
            validate_prefixed_code(new_no, max_len=MAX_CATEGORY_NO, expect_prefix=prefix)
        except ValueError as e:
            raise AppException(40001, str(e)) from e
        exists = await db.scalar(
            select(ProductCategory.id)
            .where(
                ProductCategory.store_id == int(cat.store_id),
                ProductCategory.category_no == new_no,
                ProductCategory.id != int(cat.id),
            )
            .limit(1)
        )
        if exists:
            raise AppException(40002, f"本店分类编号「{new_no}」已存在")
        cat.category_no = new_no
    if "require_category_ids" in payload:
        require_ids = await validate_require_category_ids(
            db,
            int(cat.store_id),
            payload.pop("require_category_ids"),
            self_id=int(cat.id),
        )
        cat.require_category_ids = require_ids or None
    for k, v in payload.items():
        setattr(cat, k, v)
    await db.flush()
    await _invalidate_category_menu(db, original_store_id, int(cat.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category.update",
        module="product",
        target_type="category",
        target_id=int(cat.id),
        detail={
            "store_id": int(cat.store_id) if cat.store_id else None,
            "category_no": cat.category_no,
            "fields": list(data.model_dump(exclude_unset=True).keys()),
        },
        request=request,
        store_id=int(cat.store_id) if cat.store_id else None,
    )
    return success({"id": cat.id, "store_id": cat.store_id, "category_no": cat.category_no}, request=request)
