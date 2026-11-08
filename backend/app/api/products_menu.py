"""菜单编码建议与跨店复制：从 products 路由拆出，路径仍挂在 /api。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success
from app.models import OptionGroup, Product, ProductCategory, Store
from app.schemas.product import MenuCopyRequest
from app.services.audit_service import AuditService
from app.services.menu_copy_service import MenuCopyService
from app.services.menu_cache_service import invalidate_store_menu_cache
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(tags=["餐品"])


@router.get("/menu/code-suggest", dependencies=[require_permission("product:read")])
async def suggest_menu_code(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    name: str,
    kind: str = "product",
    store_id: int | None = None,
    group_id: int | None = None,
    category_id: int | None = None,
    hq: bool = False,
):
    """根据中文名生成默认编码（拼音音节首字母大写 + 门店前缀）。

    kind: product | category | group | option
    product 时可传 category_id，生成 {前缀}-{分类逻辑}-{餐品逻辑}
    hq=true 或不传 store_id（超管）时餐品/分类前缀用 0000
    """
    from app.services.option_code import (
        HQ_STORE_PREFIX,
        group_logical_key,
        name_to_logical_code,
        normalize_store_prefix,
        suggest_code,
    )

    kind = (kind or "product").strip().lower()
    if kind not in ("product", "category", "group", "option"):
        raise AppException(40001, "kind 须为 product/category/group/option")

    prefix = HQ_STORE_PREFIX
    group_code = None
    category_logical = None

    if kind == "product":
        # 超管可不选门店 → 0000；店员强制本店
        if hq or (store_id is None and not user.store_id and getattr(user.role, "code", None) == "SUPER_ADMIN"):
            prefix = HQ_STORE_PREFIX
        else:
            sid = require_bound_store(user, store_id)
            if sid is None:
                raise AppException(40001, "请指定门店 store_id，或不选门店以使用总部前缀 0000")
            assert_store_access(user, sid)
            store = await db.get(Store, int(sid))
            prefix = normalize_store_prefix(getattr(store, "store_no", None) if store else None)
        if category_id:
            cat = await db.get(ProductCategory, int(category_id))
            if not cat:
                raise AppException(40001, "分类不存在")
            assert_store_access(user, cat.store_id)
            category_logical = group_logical_key(getattr(cat, "category_no", None) or "") or name_to_logical_code(
                cat.name
            )
    elif kind == "group" and store_id is None and not user.store_id:
        if getattr(user.role, "code", None) != "SUPER_ADMIN":
            raise AppException(40301, "账号未绑定门店")
        prefix = HQ_STORE_PREFIX
    elif kind == "category" or (kind == "group" and store_id is not None):
        sid = require_bound_store(user, store_id)
        if sid is None:
            raise AppException(40001, "请指定门店 store_id")
        assert_store_access(user, sid)
        store = await db.get(Store, int(sid))
        prefix = normalize_store_prefix(getattr(store, "store_no", None) if store else None)
    elif kind == "group":
        if user.store_id:
            store = await db.get(Store, int(user.store_id))
            prefix = normalize_store_prefix(getattr(store, "store_no", None) if store else None)
    if kind == "option":
        if not group_id:
            raise AppException(40001, "选项建议编码须传 group_id")
        group = await db.get(OptionGroup, int(group_id))
        if not group:
            raise AppException(40003, "规格组不存在")
        if group.store_id is not None:
            assert_store_access(user, group.store_id)
            store = await db.get(Store, int(group.store_id))
            prefix = normalize_store_prefix(getattr(store, "store_no", None) if store else None)
        else:
            prefix = HQ_STORE_PREFIX
        group_code = group.code

    data = suggest_code(
        name=name or "",
        store_prefix=prefix,
        kind=kind,
        group_code=group_code,
        category_logical=category_logical,
    )
    return success(data, request=request)


@router.post("/menu/copy-to-store", dependencies=[require_permission("product:create")])
async def copy_menu_to_store(data: MenuCopyRequest, request: Request, db: DbSession, user: CurrentUser):
    """批量将分类/规格组/餐品复制到另一门店（超管跨店；店员仅本店→本店无意义时拒绝）。

    - 总部共享规格组无需复制，餐品会直接沿用原组 id
    - 目标店已有同名分类 / 同 code 门店规格组：复用
    - 餐品编号冲突：on_product_conflict=skip|rename
    - 复制餐品时自动带上所属分类与绑定的私有规格组
    """
    assert_store_access(user, data.target_store_id)
    # 校验源数据门店权限
    if data.category_ids:
        cats = list(
            (
                await db.execute(
                    select(ProductCategory).where(ProductCategory.id.in_(data.category_ids))
                )
            )
            .scalars()
            .all()
        )
        for c in cats:
            assert_store_access(user, c.store_id)
    if data.option_group_ids:
        groups = list(
            (await db.execute(select(OptionGroup).where(OptionGroup.id.in_(data.option_group_ids))))
            .scalars()
            .all()
        )
        for g in groups:
            if g.store_id is not None:
                assert_store_access(user, g.store_id)
    if data.product_ids:
        prods = list(
            (await db.execute(select(Product).where(Product.id.in_(data.product_ids)))).scalars().all()
        )
        for p in prods:
            assert_store_access(user, p.store_id)
            if int(p.store_id) == int(data.target_store_id):
                raise AppException(40001, "源门店与目标门店不能相同")

    # 仅分类/规格时也禁止同店复制
    source_store: int | None = None
    if data.product_ids:
        p0 = await db.get(Product, int(data.product_ids[0]))
        if p0:
            source_store = int(p0.store_id)
    elif data.category_ids:
        c0 = await db.get(ProductCategory, int(data.category_ids[0]))
        if c0:
            source_store = int(c0.store_id)
    elif data.option_group_ids:
        g0 = await db.get(OptionGroup, int(data.option_group_ids[0]))
        if g0 and g0.store_id is not None:
            source_store = int(g0.store_id)
    if source_store is not None and int(source_store) == int(data.target_store_id):
        raise AppException(40001, "源门店与目标门店不能相同")

    result = await MenuCopyService.copy_to_store(
        db,
        target_store_id=int(data.target_store_id),
        category_ids=data.category_ids,
        option_group_ids=data.option_group_ids,
        product_ids=data.product_ids,
        on_product_conflict=data.on_product_conflict,  # type: ignore[arg-type]
        clone_shared_option_groups=bool(data.clone_shared_option_groups),
    )
    # 跨店复制会新增目标店分类/餐品/规格，必须同步清理本地缓存并推进持久代际。
    await invalidate_store_menu_cache(db, int(data.target_store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.menu.copy",
        module="product",
        target_type="store",
        target_id=int(data.target_store_id),
        detail={
            "target_store_id": int(data.target_store_id),
            "category_ids": list(data.category_ids or []),
            "option_group_ids": list(data.option_group_ids or []),
            "product_ids": list(data.product_ids or []),
            "on_product_conflict": data.on_product_conflict,
        },
        request=request,
        store_id=int(data.target_store_id),
    )
    return success(result, request=request)
