import logging

from fastapi import APIRouter, File, Request, UploadFile
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
    ProductCreate,
    ProductUpdate,
)
from app.services.audit_service import AuditService
from app.services.menu_cache_service import enqueue_menu_cache_invalidation
from app.services.store_scope import assert_store_access, require_bound_store
from app.services.taste_profile_service import profile_payload, recompute_product
from app.services.category_require import (
    normalize_id_list,
    resolve_product_requires,
)
from app.utils.upload import save_upload
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _visibility_fields(p) -> dict:
    from app.utils.product_visibility import visibility_payload

    return visibility_payload(p)


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


from app.api.products_common import assert_groups_usable_for_store as _assert_groups_usable_for_store
from app.api.products_common import store_code_prefix as _store_code_prefix
from app.api.products_categories import validate_require_category_ids as _validate_require_category_ids
# 兼容测试旧导入路径
from app.api.products_options import assert_option_group_manage as _assert_option_group_manage  # noqa: F401

router = APIRouter(tags=["餐品"])


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
            from app.core.cache_keys import products_public_rate_limit_key

            rl_key = products_public_rate_limit_key(ip, int(store_id))
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
                "category_name": cat.name if cat else None,
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
                "cost_price": str(p.cost_price) if getattr(p, "cost_price", None) is not None else None,
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
    # 公开菜单不带成本价；员工列表按字段级权限裁剪（cost_price 仅 product:cost:read）
    if staff is None:
        for it in items:
            it.pop("cost_price", None)
    else:
        from app.api.deps import get_user_permissions
        from app.services.field_policy import apply_field_policy_list

        perms = await get_user_permissions(db, staff.role_id, user_id=staff.id)
        items = apply_field_policy_list(
            items, perms, is_super_admin=staff.role.code == "SUPER_ADMIN"
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
    detail = {
        "id": product.id,
        "store_id": product.store_id,
        "store_name": store.name if store else None,
        "category_id": product.category_id,
        "category_name": cat.name if cat else None,
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
        "cost_price": str(product.cost_price) if getattr(product, "cost_price", None) is not None else None,
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
    }
    if staff is None:
        detail.pop("cost_price", None)
    else:
        from app.api.deps import get_user_permissions
        from app.services.field_policy import apply_field_policy

        perms = await get_user_permissions(db, staff.role_id, user_id=staff.id)
        apply_field_policy(
            detail, perms, is_super_admin=staff.role.code == "SUPER_ADMIN"
        )
    return success(
        detail,
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
    await enqueue_menu_cache_invalidation(db, int(product.store_id))
    return success({"id": product.id, "store_id": product.store_id, "product_no": product.product_no}, request=request)


@router.put("/products/{product_id}", dependencies=[require_permission("product:update")])
async def update_product(
    product_id: int, data: ProductUpdate, request: Request, db: DbSession, user: CurrentUser
):
    # Product.stock 是无门店覆盖时的库存主值；订单扣减会先锁 Product。
    # 管理端更新餐品（尤其 stock）也必须先取得同一行锁，避免与扣减/回库并发丢失更新。
    product = (
        await db.execute(
            select(Product)
            .where(Product.id == int(product_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
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
    await enqueue_menu_cache_invalidation(db, int(product.store_id))
    return success({"id": product.id, "store_id": product.store_id}, request=request)


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
    await enqueue_menu_cache_invalidation(db, int(product.store_id))
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

