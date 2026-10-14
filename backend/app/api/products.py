import logging

from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.api.deps import (
    CurrentUser,
    DbSession,
    OptionalCustomer,
    OptionalStaffUser,
    require_permission,
)
from app.core.exceptions import AppException
from app.core.redis_client import get_redis
from app.core.response import calc_pages, success, serialize_dt
from app.models import (
    CategorySortSchedule,
    CategorySortScheduleItem,
    DeliveryProductMap,
    Option,
    OptionGroup,
    OrderItem,
    Product,
    ProductCategory,
    ProductImage,
    ProductOptionGroup,
    ProductTasteProfile,
    Store,
    StoreProductStock,
)
from app.schemas.product import (
    CategoryCreate,
    CategoryScheduleCreate,
    CategoryScheduleUpdate,
    CategoryUpdate,
    MenuCopyRequest,
    OptionCreate,
    OptionGroupCreate,
    OptionGroupUpdate,
    OptionUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services.audit_service import AuditService
from app.services.menu_copy_service import MenuCopyService
from app.services.recommendation_service import invalidate_store_caches
from app.services.store_scope import assert_store_access, require_bound_store
from app.services.taste_profile_service import (
    profile_payload,
    recompute_product,
    recompute_store,
    upsert_manual,
)
from app.services.category_require import (
    normalize_id_list,
    normalize_require_category_ids,
    resolve_product_requires,
)
from app.services.category_schedule import (
    apply_category_schedule_sort,
    parse_hhmm,
)
from app.utils.upload import save_upload
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _fmt_time(t) -> str:
    if t is None:
        return ""
    return t.strftime("%H:%M:%S") if hasattr(t, "strftime") else str(t)


def _visibility_fields(p) -> dict:
    from app.utils.product_visibility import visibility_payload

    return visibility_payload(p)


def _schedule_payload(sched: CategorySortSchedule, items: list[CategorySortScheduleItem] | None = None) -> dict:
    item_rows = items or []
    auto_at = getattr(sched, "auto_updated_at", None)
    return {
        "id": sched.id,
        "store_id": sched.store_id,
        "name": sched.name,
        "start_time": _fmt_time(sched.start_time)[:5],
        "end_time": _fmt_time(sched.end_time)[:5],
        "priority": sched.priority,
        "status": sched.status,
        "auto_sort": int(getattr(sched, "auto_sort", 0) or 0),
        "auto_updated_at": serialize_dt(auto_at),
        "items": [
            {"category_id": r.category_id, "sort_order": r.sort_order}
            for r in sorted(item_rows, key=lambda x: (x.sort_order, x.category_id))
        ],
    }


async def _load_schedule_items(db: DbSession, schedule_ids: list[int]) -> dict[int, list[CategorySortScheduleItem]]:
    if not schedule_ids:
        return {}
    rows = list(
        (
            await db.execute(
                select(CategorySortScheduleItem).where(
                    CategorySortScheduleItem.schedule_id.in_(schedule_ids)
                )
            )
        ).scalars()
    )
    out: dict[int, list[CategorySortScheduleItem]] = {i: [] for i in schedule_ids}
    for r in rows:
        out.setdefault(int(r.schedule_id), []).append(r)
    return out


async def _replace_schedule_items(
    db: DbSession,
    schedule: CategorySortSchedule,
    items: list,
) -> None:
    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.schedule_id == int(schedule.id)
        )
    )
    seen: set[int] = set()
    for raw in items or []:
        cid = int(raw.category_id if hasattr(raw, "category_id") else raw["category_id"])
        if cid in seen:
            continue
        seen.add(cid)
        cat = await db.get(ProductCategory, cid)
        if not cat or int(cat.store_id) != int(schedule.store_id):
            raise AppException(40001, f"分类不存在或不属于本店: {cid}")
        sort_order = int(raw.sort_order if hasattr(raw, "sort_order") else raw.get("sort_order", 0))
        db.add(
            CategorySortScheduleItem(
                schedule_id=int(schedule.id),
                category_id=cid,
                sort_order=sort_order,
            )
        )


def _category_payload(cat: ProductCategory, store_name: str | None = None, *, schedule_id: int | None = None, schedule_name: str | None = None) -> dict:
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


async def _validate_require_category_ids(
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


async def _validate_require_product_ids(
    db: DbSession,
    store_id: int,
    require_ids: list[int] | None,
    *,
    self_id: int | None = None,
) -> list[int]:
    ids = normalize_id_list(require_ids)
    if not ids:
        return []
    if self_id is not None and self_id in ids:
        raise AppException(40001, "餐品不能依赖自身")
    rows = list(
        (
            await db.execute(
                select(Product).where(
                    Product.id.in_(ids),
                    Product.store_id == int(store_id),
                )
            )
        ).scalars()
    )
    found = {p.id for p in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise AppException(40001, f"依赖餐品不存在或不属于本店: {missing}")
    return ids


def _store_require_lists(
    require_category_ids: list[int] | None,
    require_product_ids: list[int] | None,
    *,
    custom: bool,
) -> tuple[list | None, list | None]:
    """custom=False 时存 NULL（继承分类）；True 时存列表（可为空列表表示明确无限制）。"""
    if not custom:
        return None, None
    return list(require_category_ids or []), list(require_product_ids or [])


async def _clear_main_product_images(db: DbSession, product_id: int) -> None:
    """换主图时移除历史 MAIN 记录，避免详情轮播仍展示旧图。"""
    await db.execute(
        delete(ProductImage).where(
            ProductImage.product_id == product_id,
            ProductImage.image_type == "MAIN",
        )
    )


def _assert_safe_product_image_url(url: str | None) -> str | None:
    """餐品主图仅允许本站 uploads 相对路径或同源 /uploads/；禁止任意外链/内网。"""
    if url is None:
        return None
    s = str(url).strip()
    if not s:
        return None
    if s.startswith("/uploads/") or s.startswith("uploads/"):
        if ".." in s or "\\" in s:
            raise AppException(40015, "餐品图片路径非法")
        return s if s.startswith("/") else f"/{s}"
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
            raise AppException(40015, "不允许使用内网图片地址")
        path = parsed.path or ""
        if not path.startswith("/uploads/"):
            raise AppException(40015, "餐品图片须为站内 uploads 地址，请先上传")
        return path
    raise AppException(40015, "餐品图片地址非法，请使用上传接口")


async def _set_main_product_image(db: DbSession, product: Product, url: str | None) -> None:
    """更新主图并同步 product_images（仅保留一条 MAIN）。"""
    safe = _assert_safe_product_image_url(url)
    await _clear_main_product_images(db, product.id)
    product.main_image = safe
    if safe:
        db.add(
            ProductImage(
                product_id=product.id,
                image_url=safe,
                image_type="MAIN",
                sort_order=0,
            )
        )


router = APIRouter(tags=["餐品"])


class StoreStockUpsert(BaseModel):
    store_id: int | None = None
    stock: int = Field(default=999, ge=0, le=999999)
    status: int = Field(default=1, ge=0, le=1)


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
    from app.models import Store

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
        row = _category_payload(
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
    prefix = await _store_code_prefix(db, int(sid))
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
    require_ids = await _validate_require_category_ids(db, int(sid), data.require_category_ids)
    cat = ProductCategory(
        store_id=int(sid),
        category_no=category_no,
        require_category_ids=require_ids or None,
        **payload,
    )
    db.add(cat)
    await db.flush()
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
    payload = data.model_dump(exclude_unset=True)
    if "store_id" in payload and payload["store_id"] is not None:
        if user.role.code != "SUPER_ADMIN":
            raise AppException(40301, "仅超管可迁移分类所属门店")
        assert_store_access(user, int(payload["store_id"]))
        cat.store_id = int(payload["store_id"])
        del payload["store_id"]
        # 迁店时同步编号前缀
        prefix = await _store_code_prefix(db, int(cat.store_id))
        cat.category_no = format_category_no(prefix, cat.category_no or cat.name)
    if "category_no" in payload and payload["category_no"] is not None:
        prefix = await _store_code_prefix(db, int(cat.store_id))
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
        require_ids = await _validate_require_category_ids(
            db,
            int(cat.store_id),
            payload.pop("require_category_ids"),
            self_id=int(cat.id),
        )
        cat.require_category_ids = require_ids or None
    for k, v in payload.items():
        setattr(cat, k, v)
    await db.flush()
    await invalidate_store_caches(int(cat.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category.update",
        module="product",
        target_type="category",
        target_id=int(cat.id),
        detail={"store_id": int(cat.store_id) if cat.store_id else None, "category_no": cat.category_no, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
        store_id=int(cat.store_id) if cat.store_id else None,
    )
    return success({"id": cat.id, "store_id": cat.store_id, "category_no": cat.category_no}, request=request)


@router.get("/category-schedules", dependencies=[require_permission("product:read")])
async def list_category_schedules(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    sid = require_bound_store(user, store_id)
    if sid is None and not user.store_id and user.role.code != "SUPER_ADMIN":
        raise AppException(40020, "请指定门店")
    q = select(CategorySortSchedule).order_by(
        CategorySortSchedule.priority.desc(),
        CategorySortSchedule.start_time,
        CategorySortSchedule.id,
    )
    if sid is not None:
        assert_store_access(user, sid)
        q = q.where(CategorySortSchedule.store_id == int(sid))
    elif user.store_id:
        q = q.where(CategorySortSchedule.store_id == int(user.store_id))
    rows = list((await db.execute(q)).scalars().all())
    items_map = await _load_schedule_items(db, [int(r.id) for r in rows])
    return success(
        [_schedule_payload(r, items_map.get(int(r.id), [])) for r in rows],
        request=request,
    )


@router.post("/category-schedules", dependencies=[require_permission("product:create")])
async def create_category_schedule(
    data: CategoryScheduleCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建时段方案")
    assert_store_access(user, sid)
    try:
        start_t = parse_hhmm(data.start_time)
        end_t = parse_hhmm(data.end_time)
    except ValueError as exc:
        raise AppException(40001, str(exc)) from exc
    sched = CategorySortSchedule(
        store_id=int(sid),
        name=data.name.strip(),
        start_time=start_t,
        end_time=end_t,
        priority=int(data.priority or 0),
        status=int(data.status if data.status is not None else 1),
        auto_sort=1 if int(data.auto_sort or 0) else 0,
    )
    if not sched.name:
        raise AppException(40001, "方案名称不能为空")
    db.add(sched)
    await db.flush()
    await _replace_schedule_items(db, sched, data.items)
    await db.flush()
    items = (await _load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await invalidate_store_caches(int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.create",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={"store_id": int(sid), "name": sched.name},
        request=request,
        store_id=int(sid),
    )
    return success(_schedule_payload(sched, items), request=request)


@router.put("/category-schedules/{schedule_id}", dependencies=[require_permission("product:update")])
async def update_category_schedule(
    schedule_id: int,
    data: CategoryScheduleUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    payload = data.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise AppException(40001, "方案名称不能为空")
        sched.name = name
    if "start_time" in payload and payload["start_time"] is not None:
        try:
            sched.start_time = parse_hhmm(payload["start_time"])
        except ValueError as exc:
            raise AppException(40001, str(exc)) from exc
    if "end_time" in payload and payload["end_time"] is not None:
        try:
            sched.end_time = parse_hhmm(payload["end_time"])
        except ValueError as exc:
            raise AppException(40001, str(exc)) from exc
    if "priority" in payload and payload["priority"] is not None:
        sched.priority = int(payload["priority"])
    if "status" in payload and payload["status"] is not None:
        sched.status = int(payload["status"])
    if "auto_sort" in payload and payload["auto_sort"] is not None:
        sched.auto_sort = 1 if int(payload["auto_sort"]) else 0
    if "items" in payload and payload["items"] is not None:
        await _replace_schedule_items(db, sched, data.items or [])
    await db.flush()
    items = (await _load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await invalidate_store_caches(int(sched.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.update",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={"store_id": int(sched.store_id) if sched.store_id else None, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
        store_id=int(sched.store_id) if sched.store_id else None,
    )
    return success(_schedule_payload(sched, items), request=request)


@router.post(
    "/category-schedules/{schedule_id}/auto-refresh",
    dependencies=[require_permission("product:update")],
)
async def auto_refresh_category_schedule(
    schedule_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """按该方案时段内历史销量立即刷新分类排序（不论是否开启 auto_sort）。"""
    from app.services.category_schedule_auto import (
        category_sort_auto_cfg,
        refresh_schedule_auto_sort,
    )

    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    cfg = await category_sort_auto_cfg(db)
    result = await refresh_schedule_auto_sort(
        db,
        sched,
        lookback_days=cfg["lookback_days"],
        soft_qty=cfg["soft_qty"],
    )
    items = (await _load_schedule_items(db, [int(sched.id)])).get(int(sched.id), [])
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.auto_refresh",
        module="product",
        target_type="category_schedule",
        target_id=int(sched.id),
        detail={
            "store_id": int(sched.store_id),
            "total_qty": result.get("total_qty"),
            "confidence": result.get("confidence"),
        },
        request=request,
        store_id=int(sched.store_id),
    )
    return success(
        {"result": result, "schedule": _schedule_payload(sched, items)},
        request=request,
    )


@router.post(
    "/category-schedules/auto-refresh",
    dependencies=[require_permission("product:update")],
)
async def auto_refresh_store_category_schedules(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = Query(None),
    only_auto: bool = Query(True),
):
    """刷新门店下启用的时段方案；默认仅处理开启「销量自动」的方案。"""
    from app.services.category_schedule_auto import (
        category_sort_auto_cfg,
        refresh_store_category_schedules,
    )

    sid = require_bound_store(user, store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再刷新")
    assert_store_access(user, sid)
    cfg = await category_sort_auto_cfg(db)
    result = await refresh_store_category_schedules(
        db,
        int(sid),
        lookback_days=cfg["lookback_days"],
        soft_qty=cfg["soft_qty"],
        only_auto=bool(only_auto),
    )
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.auto_refresh_store",
        module="product",
        target_type="store",
        target_id=int(sid),
        detail={"updated": result.get("updated"), "only_auto": bool(only_auto)},
        request=request,
        store_id=int(sid),
    )
    return success(result, request=request)


@router.delete("/category-schedules/{schedule_id}", dependencies=[require_permission("product:update")])
async def delete_category_schedule(
    schedule_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sched = await db.get(CategorySortSchedule, schedule_id)
    if not sched:
        raise AppException(40401, "时段方案不存在")
    assert_store_access(user, sched.store_id)
    await db.execute(
        delete(CategorySortScheduleItem).where(
            CategorySortScheduleItem.schedule_id == int(sched.id)
        )
    )
    await db.delete(sched)
    await db.flush()
    await invalidate_store_caches(int(sched.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.category_schedule.delete",
        module="product",
        target_type="category_schedule",
        target_id=int(schedule_id),
        detail={"store_id": int(sched.store_id) if sched.store_id else None, "name": sched.name},
        request=request,
        store_id=int(sched.store_id) if sched.store_id else None,
    )
    return success({"id": schedule_id}, request=request)


@router.get("/products")
async def list_products(
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    customer: OptionalCustomer = None,
    page: int = 1,
    page_size: int = 20,
    category_id: int | None = None,
    status: str | None = "ON_SALE",
    keyword: str | None = None,
    tag: str | None = None,
    low_stock: bool = False,
    stock_threshold: int = 10,
    store_id: int | None = None,
    sale_visible: bool = False,
):
    """餐品列表。传 store_id 时只看该店餐品，并合并分店库存覆盖（本店停售剔除）。

    员工登录时强制门店隔离（非超管忽略越权 store_id）；小程序/H5 无员工令牌时仍可按 store_id 公开查询。
    公开查询或 sale_visible=1（收银点餐）时按限时/生日规则过滤；后台餐品管理不加过滤。
    """
    if staff is not None:
        store_id = require_bound_store(staff, store_id)
    elif store_id is None:
        raise AppException(40001, "请指定门店 store_id")
    else:
        # 公开菜单按 IP+门店限流（每分钟 180 次）。点餐会连拉多页，阈值过低会把正常翻页打成 429，购物车误判下架。
        if store_id is not None:
            ip = request.client.host if request.client else "unknown"
            rl_key = f"snh:rl:products:public:{ip}:{int(store_id)}"
            try:
                r = await get_redis()
                cnt = await r.incr(rl_key)
                if cnt == 1:
                    await r.expire(rl_key, 60)
                if cnt > 180:
                    raise AppException(42901, "请求过于频繁，请稍后再试")
            except AppException:
                raise
            except Exception as e:
                # Redis 不可用时不阻塞主流程，但必须留痕；否则限流静默失效，无法排查/防爬
                logger.warning("Public product list rate limit check failed: %s", e)
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 20)), 200)
    q = select(Product)
    # 停售判定子查询必须用独立别名：低库存筛选会 outerjoin 同一 StoreProductStock 表，
    # 共用实体时相关子查询会被自动关联吞掉 FROM（no FROM clauses due to auto-correlation）
    stopped_ov = aliased(StoreProductStock)
    if store_id is not None:
        q = q.where(Product.store_id == int(store_id))
        # 本店停售覆盖必须在 SQL 层剔除（NOT EXISTS），count 与分页共用同一条件，
        # 否则先分页再在 Python 层 continue 会导致页不满、total 虚高、末页空白
        stopped_exists = (
            select(1)
            .select_from(stopped_ov)
            .where(
                stopped_ov.product_id == Product.id,
                stopped_ov.store_id == int(store_id),
                stopped_ov.status != 1,
            )
            .exists()
        )
        q = q.where(~stopped_exists)
    if category_id:
        q = q.where(Product.category_id == category_id)
    if status and status != "ALL":
        q = q.where(Product.status == status)
    if tag:
        tag_map = {
            "RECOMMENDED": Product.is_recommended,
            "HOT": Product.is_hot,
            "NO_COOKING": Product.no_cooking,
        }
        col = tag_map.get(tag.strip().upper())
        if col is not None:
            q = q.where(col == 1)
    if low_stock:
        # 门店维度：分店覆盖库存优先，回退总部库存；超管全店视角按总部库存判定
        threshold = max(0, int(stock_threshold or 0))
        if store_id is not None:
            q = q.outerjoin(
                StoreProductStock,
                and_(
                    StoreProductStock.product_id == Product.id,
                    StoreProductStock.store_id == int(store_id),
                ),
            ).where(func.coalesce(StoreProductStock.stock, Product.stock) <= threshold)
        else:
            q = q.where(Product.stock <= threshold)
    if keyword:
        kw = keyword.strip()
        q = q.where((Product.name.contains(kw)) | (Product.product_no.contains(kw)))
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    # sort_order 大量餐品默认值相同，必须补 id 兜底保证翻页稳定
    result = await db.execute(
        q.order_by(Product.sort_order, Product.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    products = list(result.scalars())
    stock_map: dict[int, StoreProductStock] = {}
    if store_id and products:
        rows = (
            await db.execute(
                select(StoreProductStock).where(
                    StoreProductStock.store_id == int(store_id),
                    StoreProductStock.product_id.in_([p.id for p in products]),
                )
            )
        ).scalars().all()
        stock_map = {r.product_id: r for r in rows}

    store_ids = list({p.store_id for p in products if p.store_id})
    store_map: dict[int, str] = {}
    if store_ids:
        store_map = {
            s.id: s.name
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }

    product_ids = [p.id for p in products]
    has_options: set[int] = set()
    if product_ids:
        has_options = set(
            (
                await db.execute(
                    select(ProductOptionGroup.product_id).where(ProductOptionGroup.product_id.in_(product_ids))
                )
            ).scalars().all()
        )

    cat_ids = list({p.category_id for p in products if p.category_id})
    cats_map: dict[int, ProductCategory] = {}
    if cat_ids:
        cats_map = {
            c.id: c
            for c in (
                await db.execute(select(ProductCategory).where(ProductCategory.id.in_(cat_ids)))
            ).scalars().all()
        }

    items = []
    for p in products:
        # 本店停售品已在 SQL 层（NOT EXISTS）剔除，此处不再二次过滤，避免 total 与实际条数不一致
        ov = stock_map.get(p.id)
        cat = cats_map.get(p.category_id)
        eff_cats, eff_prods, inherited = resolve_product_requires(p, cat)
        items.append(
            {
                "id": p.id,
                "store_id": p.store_id,
                "store_name": store_map.get(p.store_id),
                "category_id": p.category_id,
                "require_category_ids": eff_cats,
                "require_product_ids": eff_prods,
                "require_inherited": inherited,
                "product_no": p.product_no,
                "name": p.name,
                "description": p.description or "",
                "main_image": p.main_image,
                "updated_at": serialize_dt(p.updated_at),
                "price": str(p.price),
                "original_price": str(p.original_price) if p.original_price else None,
                "status": p.status,
                "is_recommended": p.is_recommended,
                "is_hot": p.is_hot,
                "no_member_discount": p.no_member_discount,
                "no_cooking": p.no_cooking,
                **_visibility_fields(p),
                "sort_order": p.sort_order,
                "sales_count": p.sales_count,
                "stock": ov.stock if ov is not None else p.stock,
                "box_price": str(p.box_price or "0.00"),
                "store_stock_override": ov is not None,
                "has_options": p.id in has_options,
            }
        )
    # 公开菜单 / 收银点餐：按限时与生日过滤；后台餐品管理（员工且未声明 sale_visible）不过滤
    apply_vis = staff is None or bool(sale_visible)
    if apply_vis:
        from app.utils.birthday import is_birthday_today, normalize_calendar, today_beijing
        from app.utils.product_visibility import filter_visible_product_dicts

        # 收银员可看到生日菜（由店员确认身份）；顾客端仅今日生日可见
        is_bday = bool(staff is not None and sale_visible)
        if customer is not None:
            is_bday = is_birthday_today(
                customer.birthday,
                normalize_calendar(getattr(customer, "birthday_calendar", None)),
            ) or is_bday
        items = filter_visible_product_dicts(
            items, today=today_beijing(), is_birthday_today=is_bday
        )
    return success(
        {
            "items": items,
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total or 0, page_size),
        },
        request=request,
    )


@router.get("/store-stocks", dependencies=[require_permission("product:read")])
async def list_store_stocks(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    page: int = 1,
    page_size: int = 100,
    keyword: str | None = None,
):
    """分店库存覆盖列表（含品牌餐品名）。keyword 匹配餐品编号/名称。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 100)), 200)
    kw = (keyword or "").strip()
    pq = select(Product).where(Product.store_id == sid)
    if kw:
        like = f"%{kw}%"
        pq = pq.where(or_(Product.product_no.like(like), Product.name.like(like)))
    products = list((await db.execute(pq.order_by(Product.sort_order, Product.id))).scalars())
    overrides = {
        r.product_id: r
        for r in (
            await db.execute(select(StoreProductStock).where(StoreProductStock.store_id == sid))
        ).scalars()
    }
    items = []
    for p in products:
        ov = overrides.get(p.id)
        items.append(
            {
                "product_id": p.id,
                "product_no": p.product_no,
                "name": p.name,
                "brand_status": p.status,
                "brand_stock": p.stock,
                "store_id": sid,
                "stock": ov.stock if ov else p.stock,
                "status": ov.status if ov else 1,
                "has_override": ov is not None,
            }
        )
    total = len(items)
    start = (page - 1) * page_size
    slice_items = items[start : start + page_size]
    return success(
        {
            "store_id": sid,
            "items": slice_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.put("/store-stocks/{product_id}", dependencies=[require_permission("product:update")])
async def upsert_store_stock(
    product_id: int,
    data: StoreStockUpsert,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    assert_store_access(user, sid)
    product = await db.get(Product, product_id)
    if not product:
        raise AppException(40401, "餐品不存在")
    if int(product.store_id) != int(sid):
        raise AppException(40301, "餐品不属于该门店")
    row = (
        await db.execute(
            select(StoreProductStock).where(
                StoreProductStock.store_id == sid,
                StoreProductStock.product_id == product_id,
            )
        )
    ).scalar_one_or_none()
    if row:
        row.stock = int(data.stock)
        row.status = int(data.status)
    else:
        row = StoreProductStock(
            store_id=sid,
            product_id=product_id,
            stock=int(data.stock),
            status=int(data.status),
        )
        db.add(row)
    await db.flush()
    await invalidate_store_caches(int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.stock.upsert",
        module="product",
        target_type="store_product_stock",
        target_id=int(product_id),
        detail={"store_id": int(sid), "product_id": int(product_id), "stock": int(row.stock), "status": int(row.status)},
        request=request,
        store_id=int(sid),
    )
    return success(
        {
            "store_id": sid,
            "product_id": product_id,
            "stock": row.stock,
            "status": row.status,
            "has_override": True,
        },
        request=request,
    )


@router.delete("/store-stocks/{product_id}", dependencies=[require_permission("product:update")])
async def delete_store_stock(
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """删除分店覆盖，回退品牌库存。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    assert_store_access(user, sid)
    await db.execute(
        delete(StoreProductStock).where(
            StoreProductStock.store_id == sid,
            StoreProductStock.product_id == product_id,
        )
    )
    await db.flush()
    await invalidate_store_caches(int(sid))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.stock.delete",
        module="product",
        target_type="store_product_stock",
        target_id=int(product_id),
        detail={"store_id": int(sid), "product_id": int(product_id)},
        request=request,
        store_id=int(sid),
    )
    return success({"deleted": True, "store_id": sid, "product_id": product_id}, request=request)


@router.get("/products/{product_id}")
async def get_product(
    product_id: int,
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    customer: OptionalCustomer = None,
):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    # 点餐页无员工令牌仍可按餐品 id 打开详情；员工令牌则限制在本店
    if staff is not None:
        assert_store_access(staff, product.store_id)
    else:
        # 公开详情：限时/生日未命中则不可打开（防深链偷看后加购）
        from app.utils.birthday import is_birthday_today, normalize_calendar, today_beijing
        from app.utils.product_visibility import is_product_visible

        is_bday = False
        if customer is not None:
            is_bday = is_birthday_today(
                customer.birthday,
                normalize_calendar(getattr(customer, "birthday_calendar", None)),
            )
        if not is_product_visible(
            product, today=today_beijing(), is_birthday_today=is_bday
        ):
            raise AppException(40002, "该餐品今日不可点（限时或生日专属）")

    pog_result = await db.execute(
        select(ProductOptionGroup, OptionGroup)
        .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
        .where(ProductOptionGroup.product_id == product_id, OptionGroup.status == 1)
        .order_by(ProductOptionGroup.sort_order)
    )
    pairs = list(pog_result.all())
    gids = [group.id for _, group in pairs]
    opts_by_group: dict[int, list] = {gid: [] for gid in gids}
    if gids:
        opts_result = await db.execute(
            select(Option)
            .where(Option.group_id.in_(gids), Option.status == 1)
            .order_by(Option.sort_order, Option.id)
        )
        for o in opts_result.scalars().all():
            opts_by_group.setdefault(int(o.group_id), []).append(o)
    option_groups = []
    for _, group in pairs:
        option_groups.append({
            "id": group.id,
            "name": group.name,
            "code": group.code,
            "selection_type": group.selection_type,
            "is_required": group.is_required,
            "options": [
                {
                    "id": o.id,
                    "name": o.name,
                    "code": o.code,
                    "extra_price": str(o.extra_price),
                    "is_default": bool(o.is_default),
                }
                for o in opts_by_group.get(group.id, [])
            ],
        })

    imgs_result = await db.execute(
        select(ProductImage).where(ProductImage.product_id == product_id).order_by(ProductImage.sort_order)
    )
    taste_profile_row = (
        await db.execute(
            select(ProductTasteProfile).where(ProductTasteProfile.product_id == product_id)
        )
    ).scalar_one_or_none()
    option_group_ids = gids
    store = await db.get(Store, product.store_id) if product.store_id else None
    cat = await db.get(ProductCategory, product.category_id) if product.category_id else None
    eff_cats, eff_prods, inherited = resolve_product_requires(product, cat)
    stored_cats = (
        normalize_id_list(product.require_category_ids)
        if product.require_category_ids is not None
        else None
    )
    stored_prods = (
        normalize_id_list(product.require_product_ids)
        if product.require_product_ids is not None
        else None
    )
    return success(
        {
            "id": product.id,
            "store_id": product.store_id,
            "store_name": store.name if store else None,
            "category_id": product.category_id,
            "require_category_ids": eff_cats,
            "require_product_ids": eff_prods,
            "require_inherited": inherited,
            "require_category_ids_stored": stored_cats,
            "require_product_ids_stored": stored_prods,
            "product_no": product.product_no,
            "name": product.name,
            "description": product.description or "",
            "main_image": product.main_image,
            "updated_at": serialize_dt(product.updated_at),
            "price": str(product.price),
            "original_price": str(product.original_price) if product.original_price else None,
            "status": product.status,
            "is_recommended": product.is_recommended,
            "is_hot": product.is_hot,
            "no_member_discount": product.no_member_discount,
            "no_cooking": product.no_cooking,
            **_visibility_fields(product),
            "sort_order": product.sort_order,
            "stock": product.stock,
            "box_price": str(product.box_price or "0.00"),
            "option_group_ids": option_group_ids,
            "option_groups": option_groups,
            "images": [{"id": i.id, "url": i.image_url, "type": i.image_type} for i in imgs_result.scalars()],
            "taste_profile": profile_payload(taste_profile_row),
        },
        request=request,
    )


@router.post("/products", dependencies=[require_permission("product:create")])
async def create_product(data: ProductCreate, request: Request, db: DbSession, user: CurrentUser):
    from app.services.option_code import (
        HQ_STORE_PREFIX,
        MAX_PRODUCT_NO,
        format_product_no,
        group_logical_key,
        name_to_logical_code,
        validate_prefixed_code,
    )

    cat = await db.get(ProductCategory, data.category_id)
    if not cat:
        raise AppException(40001, "分类不存在，请先创建分类")

    # 超管可不选门店：编号前缀 0000；餐品归属所选分类的门店
    use_hq_prefix = False
    sid = require_bound_store(user, data.store_id)
    if sid is None and user.store_id:
        sid = int(user.store_id)
    if sid is None:
        if getattr(user.role, "code", None) != "SUPER_ADMIN":
            raise AppException(40020, "请指定门店后再创建餐品")
        use_hq_prefix = True
        sid = int(cat.store_id)
    else:
        if int(cat.store_id) != int(sid):
            raise AppException(40001, "分类不属于所选门店，请重新选择分类")
    assert_store_access(user, sid)

    prefix = HQ_STORE_PREFIX if use_hq_prefix else await _store_code_prefix(db, int(sid))
    cat_logical = group_logical_key(getattr(cat, "category_no", None) or "") or name_to_logical_code(cat.name)
    raw_no = (data.product_no or "").strip() or name_to_logical_code(data.name)
    try:
        product_no = format_product_no(prefix, raw_no, category_logical=cat_logical)
        validate_prefixed_code(product_no, max_len=MAX_PRODUCT_NO, expect_prefix=prefix)
    except ValueError as e:
        raise AppException(40001, str(e)) from e
    dup = await db.execute(
        select(Product.id).where(Product.store_id == sid, Product.product_no == product_no)
    )
    if dup.scalar_one_or_none():
        raise AppException(40002, f"本店餐品编号「{product_no}」已存在，请换一个")
    if data.option_group_ids:
        await _assert_groups_usable_for_store(db, data.option_group_ids, int(sid))

    # 未传依赖字段：存 NULL，运行时继承分类默认（方案 A）；勿把分类列表写入餐品
    if data.require_category_ids is None and data.require_product_ids is None:
        stored_cats, stored_prods = _store_require_lists(None, None, custom=False)
    else:
        req_cats = await _validate_require_category_ids(db, int(sid), data.require_category_ids)
        req_prods = await _validate_require_product_ids(db, int(sid), data.require_product_ids)
        stored_cats, stored_prods = _store_require_lists(req_cats, req_prods, custom=True)

    try:
        payload = data.model_dump(
            exclude={"option_group_ids", "store_id", "require_category_ids", "require_product_ids"}
        )
        payload["product_no"] = product_no
        product = Product(
            store_id=int(sid),
            require_category_ids=stored_cats,
            require_product_ids=stored_prods,
            **payload,
        )
        db.add(product)
        await db.flush()
        for idx, gid in enumerate(data.option_group_ids):
            db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(40002, f"本店餐品编号「{product_no}」已存在，请换一个")
    await AuditService.log_staff(
        db,
        user=user,
        action="product.create",
        module="product",
        target_type="product",
        target_id=int(product.id),
        detail={"store_id": int(product.store_id), "product_no": product.product_no, "name": data.name},
        request=request,
        store_id=int(product.store_id),
    )
    try:
        await recompute_product(db, int(product.id))
    except Exception:
        logger.exception("新品口味画像自动推导失败 product_id=%s", product.id)
    await invalidate_store_caches(int(product.store_id))
    return success({"id": product.id, "store_id": product.store_id, "product_no": product.product_no}, request=request)


@router.put("/products/{product_id}", dependencies=[require_permission("product:update")])
async def update_product(
    product_id: int, data: ProductUpdate, request: Request, db: DbSession, user: CurrentUser
):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    assert_store_access(user, product.store_id)

    payload = data.model_dump(
        exclude_unset=True,
        exclude={"option_group_ids", "require_category_ids", "require_product_ids"},
    )
    from app.services.option_code import (
        MAX_PRODUCT_NO,
        format_product_no,
        group_logical_key,
        name_to_logical_code,
        validate_prefixed_code,
    )

    async def _product_cat_logical(store_id: int, category_id: int | None = None) -> str:
        cid = category_id or product.category_id
        c = await db.get(ProductCategory, int(cid))
        if not c:
            return "Item"
        return group_logical_key(getattr(c, "category_no", None) or "") or name_to_logical_code(c.name)

    if "store_id" in payload and payload["store_id"] is not None:
        if user.role.code != "SUPER_ADMIN":
            raise AppException(40301, "仅超管可迁移餐品所属门店")
        new_sid = int(payload["store_id"])
        assert_store_access(user, new_sid)
        product.store_id = new_sid
        del payload["store_id"]
        # 迁店时同步改编号前缀（保留分类+餐品逻辑）
        if "product_no" not in payload:
            prefix = await _store_code_prefix(db, int(product.store_id))
            cat_logical = await _product_cat_logical(int(product.store_id))
            try:
                product.product_no = format_product_no(prefix, product.product_no, category_logical=cat_logical)
                validate_prefixed_code(product.product_no, max_len=MAX_PRODUCT_NO, expect_prefix=prefix)
            except ValueError as e:
                raise AppException(40001, str(e)) from e

    if "product_no" in payload and payload["product_no"] is not None:
        prefix = await _store_code_prefix(db, int(product.store_id))
        cat_logical = await _product_cat_logical(
            int(product.store_id),
            payload.get("category_id", product.category_id),
        )
        try:
            new_no = format_product_no(prefix, str(payload.pop("product_no")), category_logical=cat_logical)
            validate_prefixed_code(new_no, max_len=MAX_PRODUCT_NO, expect_prefix=prefix)
        except ValueError as e:
            raise AppException(40001, str(e)) from e
        dup = await db.scalar(
            select(Product.id).where(
                Product.store_id == product.store_id,
                Product.product_no == new_no,
                Product.id != product.id,
            ).limit(1)
        )
        if dup:
            raise AppException(40002, f"本店餐品编号「{new_no}」已存在，请换一个")
        product.product_no = new_no

    if "category_id" in payload and payload["category_id"] is not None:
        cat = await db.get(ProductCategory, int(payload["category_id"]))
        if not cat:
            raise AppException(40001, "分类不存在")
        if int(cat.store_id) != int(product.store_id):
            raise AppException(40001, "分类与餐品不在同一门店")

    raw = data.model_dump(exclude_unset=True)
    if "require_category_ids" in raw or "require_product_ids" in raw:
        req_cats = await _validate_require_category_ids(
            db,
            int(product.store_id),
            raw.get("require_category_ids", product.require_category_ids),
        )
        req_prods = await _validate_require_product_ids(
            db,
            int(product.store_id),
            raw.get("require_product_ids", product.require_product_ids),
            self_id=int(product.id),
        )
        product.require_category_ids, product.require_product_ids = _store_require_lists(
            req_cats, req_prods, custom=True
        )

    old_main_image = product.main_image
    for k, v in payload.items():
        if k == "main_image":
            continue
        setattr(product, k, v)
    if "main_image" in payload:
        new_main_image = payload["main_image"] or None
        if new_main_image != old_main_image:
            await _set_main_product_image(db, product, new_main_image)
    if data.option_group_ids is not None:
        if data.option_group_ids:
            await _assert_groups_usable_for_store(db, data.option_group_ids, int(product.store_id))
        await db.execute(delete(ProductOptionGroup).where(ProductOptionGroup.product_id == product_id))
        for idx, gid in enumerate(data.option_group_ids):
            db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="product.update",
        module="product",
        target_type="product",
        target_id=int(product.id),
        detail={"store_id": int(product.store_id), "product_no": product.product_no, "fields": list(data.model_dump(exclude_unset=True).keys())},
        request=request,
        store_id=int(product.store_id),
    )
    try:
        await recompute_product(db, int(product.id))
    except Exception:
        logger.exception("餐品更新后口味画像重算失败 product_id=%s", product.id)
    await invalidate_store_caches(int(product.store_id))
    return success({"id": product.id, "store_id": product.store_id}, request=request)


class TasteProfileManualUpdate(BaseModel):
    dimensions: dict[str, int] | None = None
    tags_add: list[str] = Field(default_factory=list)
    tags_remove: list[str] = Field(default_factory=list)
    reset_dimensions: list[str] = Field(default_factory=list)
    reset_tags: list[str] = Field(default_factory=list)


class TasteProfileRebuildRequest(BaseModel):
    store_id: int | None = None


@router.put("/products/{product_id}/taste-profile", dependencies=[require_permission("product:update")])
async def update_product_taste_profile(
    product_id: int,
    data: TasteProfileManualUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    assert_store_access(user, product.store_id)
    profile = await upsert_manual(
        db,
        product_id,
        dimensions=data.dimensions,
        tags_add=data.tags_add,
        tags_remove=data.tags_remove,
        reset_dimensions=data.reset_dimensions,
        reset_tags=data.reset_tags,
    )
    await invalidate_store_caches(int(product.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.taste_profile.update",
        module="product",
        target_type="product",
        target_id=product_id,
        detail={
            "dimensions": data.dimensions or {},
            "tags_add": data.tags_add,
            "tags_remove": data.tags_remove,
            "reset_dimensions": data.reset_dimensions,
            "reset_tags": data.reset_tags,
        },
        request=request,
        store_id=int(product.store_id),
    )
    return success(profile_payload(profile), request=request)


@router.post("/products/taste-profiles/rebuild", dependencies=[require_permission("product:update")])
async def rebuild_store_taste_profiles(
    data: TasteProfileRebuildRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """整店口味画像 AUTO 重算（保留 MANUAL 接管键）。超管需显式指定 store_id。"""
    store_id = data.store_id
    if user.role.code == "SUPER_ADMIN":
        if not store_id:
            raise AppException(40001, "请指定需要重算的门店")
    else:
        if not user.store_id:
            raise AppException(40301, "账号未绑定门店", status_code=403)
        store_id = int(user.store_id)
    store_id = int(store_id)
    store = await db.get(Store, store_id)
    if not store:
        raise AppException(40004, "门店不存在")
    rebuilt = await recompute_store(db, store_id)
    await invalidate_store_caches(store_id)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.taste_profile.rebuild",
        module="product",
        target_type="store",
        target_id=store_id,
        detail={"store_id": store_id, "rebuilt": rebuilt},
        request=request,
        store_id=store_id,
    )
    return success({"store_id": store_id, "rebuilt": rebuilt}, request=request)


@router.post("/products/{product_id}/image", dependencies=[require_permission("product:update")])
async def upload_product_image(
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
):
    """上传餐品主图（同时写入 product_images 作详情预览）。"""
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    assert_store_access(user, product.store_id)
    url = await save_upload(file, subdir="products")
    await _set_main_product_image(db, product, url)
    await db.flush()
    await db.refresh(product)
    updated_at = serialize_dt(product.updated_at)
    await AuditService.log_staff(
        db,
        user=user,
        action="product.image.upload",
        module="product",
        target_type="product",
        target_id=int(product.id),
        detail={"store_id": int(product.store_id), "product_id": int(product.id), "main_image": url},
        request=request,
        store_id=int(product.store_id),
    )
    return success(
        {
            "id": product.id,
            "main_image": url,
            "url": url,
            "updated_at": updated_at,
        },
        request=request,
    )


@router.delete("/products/{product_id}", dependencies=[require_permission("product:delete")])
async def delete_product(product_id: int, request: Request, db: DbSession, user: CurrentUser):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="餐品不存在", request=request)
    assert_store_access(user, product.store_id)
    order_count = await db.scalar(
        select(func.count()).select_from(OrderItem).where(OrderItem.product_id == product_id)
    )
    if order_count:
        raise AppException(40004, "该餐品已有订单记录，无法删除，请改为下架")
    await db.execute(delete(DeliveryProductMap).where(DeliveryProductMap.product_id == product_id))
    await db.delete(product)
    await db.flush()
    await invalidate_store_caches(int(product.store_id))
    await AuditService.log_staff(
        db,
        user=user,
        action="product.delete",
        module="product",
        target_type="product",
        target_id=int(product_id),
        detail={"store_id": int(product.store_id), "product_no": product.product_no, "name": product.name},
        request=request,
        store_id=int(product.store_id),
    )
    return success({"id": product_id}, request=request)


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


async def _assert_groups_usable_for_store(db: DbSession, group_ids: list[int], store_id: int | None) -> None:
    """餐品只能绑总部共享或本店规格组。"""
    if not group_ids:
        return
    rows = list(
        (
            await db.execute(
                select(OptionGroup).where(OptionGroup.id.in_(group_ids), OptionGroup.status == 1)
            )
        ).scalars().all()
    )
    found = {int(g.id): g for g in rows}
    missing = [gid for gid in group_ids if int(gid) not in found]
    if missing:
        raise AppException(40003, f"规格组不存在或已禁用: {missing}")
    if store_id is None:
        return
    sid = int(store_id)
    bad = [
        g.id
        for g in rows
        if g.store_id is not None and int(g.store_id) != sid
    ]
    if bad:
        raise AppException(40003, f"规格组不属于本店或总部共享: {bad}")


def _assert_option_group_manage(user: CurrentUser, group: OptionGroup | None = None) -> None:
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


async def _store_code_prefix(db: DbSession, store_id: int | None) -> str:
    from app.services.option_code import HQ_STORE_PREFIX, normalize_store_prefix

    if store_id is None:
        return HQ_STORE_PREFIX
    store = await db.get(Store, int(store_id))
    return normalize_store_prefix(getattr(store, "store_no", None) if store else None)


async def _normalize_group_code(db: DbSession, raw: str, store_id: int | None) -> str:
    from app.services.option_code import MAX_GROUP_CODE, format_group_code, validate_prefixed_code

    prefix = await _store_code_prefix(db, store_id)
    try:
        code = format_group_code(prefix, raw)
        return validate_prefixed_code(code, max_len=MAX_GROUP_CODE, expect_prefix=prefix)
    except ValueError as e:
        raise AppException(40001, str(e)) from e


async def _normalize_option_code(db: DbSession, raw: str, group: OptionGroup) -> str:
    from app.services.option_code import MAX_OPTION_CODE, format_option_code, validate_prefixed_code

    prefix = await _store_code_prefix(db, group.store_id)
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
    _assert_option_group_manage(user, group)
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
    _assert_option_group_manage(user, group)
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
    _assert_option_group_manage(user, group)
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
    _assert_option_group_manage(user, group)
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
    _assert_option_group_manage(user, group)
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
    _assert_option_group_manage(user, group)
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
