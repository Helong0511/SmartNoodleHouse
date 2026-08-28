from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession, OptionalStaffUser, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import (
    DeliveryProductMap,
    Option,
    OptionGroup,
    OrderItem,
    Product,
    ProductCategory,
    ProductImage,
    ProductOptionGroup,
    Store,
    StoreProductStock,
)
from app.schemas.product import (
    CategoryCreate,
    CategoryUpdate,
    OptionCreate,
    OptionGroupCreate,
    OptionGroupUpdate,
    OptionUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services.store_scope import assert_store_access, require_bound_store
from app.utils.upload import save_upload

router = APIRouter(tags=["商品"])


class StoreStockUpsert(BaseModel):
    store_id: int | None = None
    stock: int = Field(default=999, ge=0, le=999999)
    status: int = Field(default=1, ge=0, le=1)


@router.get("/categories")
async def list_categories(
    request: Request,
    db: DbSession,
    status: int | None = 1,
    all: bool = False,
    store_id: int | None = None,
):
    """分类列表。小程序/点餐传 store_id；后台按门店筛选。"""
    from app.models import Store

    q = select(ProductCategory).order_by(ProductCategory.sort_order, ProductCategory.id)
    if not all and status is not None:
        q = q.where(ProductCategory.status == status)
    if store_id is not None:
        q = q.where(ProductCategory.store_id == int(store_id))
    result = await db.execute(q)
    cats = list(result.scalars().all())
    store_ids = list({c.store_id for c in cats if c.store_id})
    store_map: dict[int, str] = {}
    if store_ids:
        store_map = {
            s.id: s.name
            for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()
        }
    return success(
        [
            {
                "id": c.id,
                "store_id": c.store_id,
                "store_name": store_map.get(c.store_id),
                "name": c.name,
                "description": c.description,
                "sort_order": c.sort_order,
                "status": c.status,
            }
            for c in cats
        ],
        request=request,
    )


@router.post("/categories", dependencies=[require_permission("product:create")])
async def create_category(data: CategoryCreate, request: Request, db: DbSession, user: CurrentUser):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建分类")
    assert_store_access(user, sid)
    payload = data.model_dump(exclude={"store_id"})
    cat = ProductCategory(store_id=int(sid), **payload)
    db.add(cat)
    await db.flush()
    return success({"id": cat.id, "store_id": cat.store_id}, request=request)


@router.put("/categories/{cat_id}", dependencies=[require_permission("product:update")])
async def update_category(cat_id: int, data: CategoryUpdate, request: Request, db: DbSession, user: CurrentUser):
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
    for k, v in payload.items():
        setattr(cat, k, v)
    await db.flush()
    return success({"id": cat.id, "store_id": cat.store_id}, request=request)


@router.get("/products")
async def list_products(
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    page: int = 1,
    page_size: int = 20,
    category_id: int | None = None,
    status: str | None = "ON_SALE",
    keyword: str | None = None,
    store_id: int | None = None,
):
    """商品列表。传 store_id 时只看该店商品，并合并分店库存覆盖（本店停售剔除）。

    员工登录时强制门店隔离（非超管忽略越权 store_id）；小程序/H5 无员工令牌时仍可按 store_id 公开查询。
    """
    if staff is not None:
        store_id = require_bound_store(staff, store_id)
    q = select(Product)
    if store_id is not None:
        q = q.where(Product.store_id == int(store_id))
    if category_id:
        q = q.where(Product.category_id == category_id)
    if status and status != "ALL":
        q = q.where(Product.status == status)
    if keyword:
        kw = keyword.strip()
        q = q.where((Product.name.contains(kw)) | (Product.product_no.contains(kw)))
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Product.sort_order).offset((page - 1) * page_size).limit(page_size))
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

    items = []
    for p in products:
        ov = stock_map.get(p.id)
        if ov is not None and int(ov.status) != 1:
            continue
        items.append(
            {
                "id": p.id,
                "store_id": p.store_id,
                "store_name": store_map.get(p.store_id),
                "category_id": p.category_id,
                "product_no": p.product_no,
                "name": p.name,
                "description": p.description or "",
                "main_image": p.main_image,
                "price": str(p.price),
                "original_price": str(p.original_price) if p.original_price else None,
                "status": p.status,
                "is_recommended": p.is_recommended,
                "is_hot": p.is_hot,
                "sort_order": p.sort_order,
                "sales_count": p.sales_count,
                "stock": ov.stock if ov is not None else p.stock,
                "box_price": str(p.box_price or "0.00"),
                "store_stock_override": ov is not None,
                "has_options": p.id in has_options,
            }
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
):
    """分店库存覆盖列表（含品牌商品名）。"""
    sid = require_bound_store(user, store_id)
    if sid is None:
        raise AppException(40060, "请指定门店")
    products = list(
        (
            await db.execute(
                select(Product).where(Product.store_id == sid).order_by(Product.sort_order, Product.id)
            )
        ).scalars()
    )
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
    return success({"store_id": sid, "items": items}, request=request)


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
        raise AppException(40401, "商品不存在")
    if int(product.store_id) != int(sid):
        raise AppException(40301, "商品不属于该门店")
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
    return success({"deleted": True, "store_id": sid, "product_id": product_id}, request=request)


@router.get("/products/{product_id}")
async def get_product(product_id: int, request: Request, db: DbSession):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="商品不存在", request=request)

    pog_result = await db.execute(
        select(ProductOptionGroup, OptionGroup)
        .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
        .where(ProductOptionGroup.product_id == product_id, OptionGroup.status == 1)
        .order_by(ProductOptionGroup.sort_order)
    )
    option_groups = []
    for _, group in pog_result.all():
        opts_result = await db.execute(
            select(Option).where(Option.group_id == group.id, Option.status == 1).order_by(Option.sort_order)
        )
        option_groups.append({
            "id": group.id,
            "name": group.name,
            "code": group.code,
            "selection_type": group.selection_type,
            "is_required": group.is_required,
            "options": [
                {"id": o.id, "name": o.name, "code": o.code, "extra_price": str(o.extra_price)}
                for o in opts_result.scalars()
            ],
        })

    imgs_result = await db.execute(
        select(ProductImage).where(ProductImage.product_id == product_id).order_by(ProductImage.sort_order)
    )
    pog_ids_result = await db.execute(
        select(ProductOptionGroup.group_id)
        .where(ProductOptionGroup.product_id == product_id)
        .order_by(ProductOptionGroup.sort_order)
    )
    option_group_ids = [row[0] for row in pog_ids_result.all()]
    store = await db.get(Store, product.store_id) if product.store_id else None
    return success(
        {
            "id": product.id,
            "store_id": product.store_id,
            "store_name": store.name if store else None,
            "category_id": product.category_id,
            "product_no": product.product_no,
            "name": product.name,
            "description": product.description or "",
            "main_image": product.main_image,
            "price": str(product.price),
            "original_price": str(product.original_price) if product.original_price else None,
            "status": product.status,
            "is_recommended": product.is_recommended,
            "is_hot": product.is_hot,
            "sort_order": product.sort_order,
            "stock": product.stock,
            "box_price": str(product.box_price or "0.00"),
            "option_group_ids": option_group_ids,
            "option_groups": option_groups,
            "images": [{"id": i.id, "url": i.image_url, "type": i.image_type} for i in imgs_result.scalars()],
        },
        request=request,
    )


@router.post("/products", dependencies=[require_permission("product:create")])
async def create_product(data: ProductCreate, request: Request, db: DbSession, user: CurrentUser):
    sid = require_bound_store(user, data.store_id)
    if sid is None:
        if user.store_id:
            sid = int(user.store_id)
        else:
            raise AppException(40020, "请指定门店后再创建商品")
    assert_store_access(user, sid)

    cat = await db.get(ProductCategory, data.category_id)
    if not cat:
        raise AppException(40001, "分类不存在，请先创建分类")
    if int(cat.store_id) != int(sid):
        raise AppException(40001, "分类不属于所选门店，请重新选择分类")

    product_no = data.product_no.strip()
    dup = await db.execute(
        select(Product.id).where(Product.store_id == sid, Product.product_no == product_no)
    )
    if dup.scalar_one_or_none():
        raise AppException(40002, f"本店商品编号「{product_no}」已存在，请换一个")
    if data.option_group_ids:
        found = await db.execute(
            select(OptionGroup.id).where(OptionGroup.id.in_(data.option_group_ids), OptionGroup.status == 1)
        )
        found_ids = set(found.scalars().all())
        missing = [gid for gid in data.option_group_ids if gid not in found_ids]
        if missing:
            raise AppException(40003, f"规格组不存在或已禁用: {missing}")
    try:
        payload = data.model_dump(exclude={"option_group_ids", "store_id"})
        payload["product_no"] = product_no
        product = Product(store_id=int(sid), **payload)
        db.add(product)
        await db.flush()
        for idx, gid in enumerate(data.option_group_ids):
            db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppException(40002, f"本店商品编号「{product_no}」已存在，请换一个")
    return success({"id": product.id, "store_id": product.store_id}, request=request)


@router.put("/products/{product_id}", dependencies=[require_permission("product:update")])
async def update_product(
    product_id: int, data: ProductUpdate, request: Request, db: DbSession, user: CurrentUser
):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
    assert_store_access(user, product.store_id)

    payload = data.model_dump(exclude_unset=True, exclude={"option_group_ids"})
    if "store_id" in payload and payload["store_id"] is not None:
        if user.role.code != "SUPER_ADMIN":
            raise AppException(40301, "仅超管可迁移商品所属门店")
        new_sid = int(payload["store_id"])
        assert_store_access(user, new_sid)
        product.store_id = new_sid
        del payload["store_id"]

    if "category_id" in payload and payload["category_id"] is not None:
        cat = await db.get(ProductCategory, int(payload["category_id"]))
        if not cat:
            raise AppException(40001, "分类不存在")
        if int(cat.store_id) != int(product.store_id):
            raise AppException(40001, "分类与商品不在同一门店")

    for k, v in payload.items():
        setattr(product, k, v)
    if data.option_group_ids is not None:
        await db.execute(delete(ProductOptionGroup).where(ProductOptionGroup.product_id == product_id))
        for idx, gid in enumerate(data.option_group_ids):
            db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
    await db.flush()
    return success({"id": product.id, "store_id": product.store_id}, request=request)


@router.post("/products/{product_id}/image", dependencies=[require_permission("product:update")])
async def upload_product_image(
    product_id: int,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
):
    """上传商品主图（同时写入 product_images 作详情预览）。"""
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
    assert_store_access(user, product.store_id)
    url = await save_upload(file, subdir="products")
    product.main_image = url
    db.add(
        ProductImage(
            product_id=product.id,
            image_url=url,
            image_type="MAIN",
            sort_order=0,
        )
    )
    await db.flush()
    return success({"id": product.id, "main_image": url, "url": url}, request=request)


@router.delete("/products/{product_id}", dependencies=[require_permission("product:delete")])
async def delete_product(product_id: int, request: Request, db: DbSession, user: CurrentUser):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
    assert_store_access(user, product.store_id)
    order_count = await db.scalar(
        select(func.count()).select_from(OrderItem).where(OrderItem.product_id == product_id)
    )
    if order_count:
        raise AppException(40004, "该商品已有订单记录，无法删除，请改为下架")
    await db.execute(delete(DeliveryProductMap).where(DeliveryProductMap.product_id == product_id))
    await db.delete(product)
    await db.flush()
    return success({"id": product_id}, request=request)


@router.get("/options/groups")
async def list_option_groups(request: Request, db: DbSession, include_disabled: bool = False):
    q = select(OptionGroup).order_by(OptionGroup.sort_order)
    if not include_disabled:
        q = q.where(OptionGroup.status == 1)
    result = await db.execute(q)
    groups = []
    for g in result.scalars():
        opts_q = select(Option).where(Option.group_id == g.id).order_by(Option.sort_order)
        if not include_disabled:
            opts_q = opts_q.where(Option.status == 1)
        opts = await db.execute(opts_q)
        groups.append({
            "id": g.id,
            "name": g.name,
            "code": g.code,
            "selection_type": g.selection_type,
            "is_required": g.is_required,
            "sort_order": g.sort_order,
            "status": g.status,
            "options": [
                {
                    "id": o.id,
                    "group_id": o.group_id,
                    "name": o.name,
                    "code": o.code,
                    "extra_price": str(o.extra_price),
                    "sort_order": o.sort_order,
                    "status": o.status,
                }
                for o in opts.scalars()
            ],
        })
    return success(groups, request=request)


@router.post("/options/groups", dependencies=[require_permission("product:create")])
async def create_option_group(data: OptionGroupCreate, request: Request, db: DbSession):
    group = OptionGroup(**data.model_dump())
    db.add(group)
    await db.flush()
    return success({"id": group.id}, request=request)


@router.post("/options", dependencies=[require_permission("product:create")])
async def create_option(data: OptionCreate, request: Request, db: DbSession):
    opt = Option(**data.model_dump())
    db.add(opt)
    await db.flush()
    return success({"id": opt.id}, request=request)


@router.put("/options/groups/{group_id}", dependencies=[require_permission("product:update")])
async def update_option_group(group_id: int, data: OptionGroupUpdate, request: Request, db: DbSession):
    group = await db.get(OptionGroup, group_id)
    if not group:
        return success(None, message="规格组不存在", request=request)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(group, k, v)
    await db.flush()
    return success({"id": group.id}, request=request)


@router.delete("/options/groups/{group_id}", dependencies=[require_permission("product:delete")])
async def delete_option_group(group_id: int, request: Request, db: DbSession):
    group = await db.get(OptionGroup, group_id)
    if not group:
        return success(None, message="规格组不存在", request=request)
    group.status = 0
    opts = await db.execute(select(Option).where(Option.group_id == group_id))
    for o in opts.scalars():
        o.status = 0
    await db.flush()
    return success({"id": group.id}, request=request)


@router.put("/options/{option_id}", dependencies=[require_permission("product:update")])
async def update_option(option_id: int, data: OptionUpdate, request: Request, db: DbSession):
    opt = await db.get(Option, option_id)
    if not opt:
        return success(None, message="规格选项不存在", request=request)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(opt, k, v)
    await db.flush()
    return success({"id": opt.id}, request=request)


@router.delete("/options/{option_id}", dependencies=[require_permission("product:delete")])
async def delete_option(option_id: int, request: Request, db: DbSession):
    opt = await db.get(Option, option_id)
    if not opt:
        return success(None, message="规格选项不存在", request=request)
    opt.status = 0
    await db.flush()
    return success({"id": opt.id}, request=request)
