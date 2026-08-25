from fastapi import APIRouter, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import calc_pages, success
from app.models import Option, OptionGroup, Product, ProductCategory, ProductImage, ProductOptionGroup
from app.schemas.product import CategoryCreate, CategoryUpdate, OptionCreate, OptionGroupCreate, ProductCreate, ProductUpdate

router = APIRouter(tags=["商品"])


@router.get("/categories")
async def list_categories(request: Request, db: DbSession, status: int | None = 1):
    q = select(ProductCategory).order_by(ProductCategory.sort_order)
    if status is not None:
        q = q.where(ProductCategory.status == status)
    result = await db.execute(q)
    return success([{"id": c.id, "name": c.name, "description": c.description, "sort_order": c.sort_order, "status": c.status} for c in result.scalars()], request=request)


@router.post("/categories", dependencies=[require_permission("product:create")])
async def create_category(data: CategoryCreate, request: Request, db: DbSession, user: CurrentUser):
    cat = ProductCategory(**data.model_dump())
    db.add(cat)
    await db.flush()
    return success({"id": cat.id}, request=request)


@router.put("/categories/{cat_id}", dependencies=[require_permission("product:update")])
async def update_category(cat_id: int, data: CategoryUpdate, request: Request, db: DbSession):
    cat = await db.get(ProductCategory, cat_id)
    if not cat:
        return success(None, message="分类不存在", request=request)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    await db.flush()
    return success({"id": cat.id}, request=request)


@router.get("/products")
async def list_products(
    request: Request,
    db: DbSession,
    page: int = 1,
    page_size: int = 20,
    category_id: int | None = None,
    status: str | None = "ON_SALE",
    keyword: str | None = None,
):
    q = select(Product)
    if category_id:
        q = q.where(Product.category_id == category_id)
    if status:
        q = q.where(Product.status == status)
    if keyword:
        q = q.where(Product.name.contains(keyword))
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.order_by(Product.sort_order).offset((page - 1) * page_size).limit(page_size))
    items = [
        {
            "id": p.id,
            "category_id": p.category_id,
            "product_no": p.product_no,
            "name": p.name,
            "description": p.description,
            "main_image": p.main_image,
            "price": str(p.price),
            "original_price": str(p.original_price) if p.original_price else None,
            "status": p.status,
            "is_recommended": p.is_recommended,
            "is_hot": p.is_hot,
            "sort_order": p.sort_order,
            "sales_count": p.sales_count,
        }
        for p in result.scalars()
    ]
    return success({"items": items, "total": total or 0, "page": page, "page_size": page_size, "total_pages": calc_pages(total or 0, page_size)}, request=request)


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
    return success(
        {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "main_image": product.main_image,
            "price": str(product.price),
            "status": product.status,
            "option_groups": option_groups,
            "images": [{"id": i.id, "url": i.image_url, "type": i.image_type} for i in imgs_result.scalars()],
        },
        request=request,
    )


@router.post("/products", dependencies=[require_permission("product:create")])
async def create_product(data: ProductCreate, request: Request, db: DbSession):
    product = Product(**data.model_dump(exclude={"option_group_ids"}))
    db.add(product)
    await db.flush()
    for idx, gid in enumerate(data.option_group_ids):
        db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
    await db.flush()
    return success({"id": product.id}, request=request)


@router.put("/products/{product_id}", dependencies=[require_permission("product:update")])
async def update_product(product_id: int, data: ProductUpdate, request: Request, db: DbSession):
    product = await db.get(Product, product_id)
    if not product:
        return success(None, message="商品不存在", request=request)
    for k, v in data.model_dump(exclude_unset=True, exclude={"option_group_ids"}).items():
        setattr(product, k, v)
    if data.option_group_ids is not None:
        await db.execute(select(ProductOptionGroup).where(ProductOptionGroup.product_id == product_id))
        from sqlalchemy import delete
        await db.execute(delete(ProductOptionGroup).where(ProductOptionGroup.product_id == product_id))
        for idx, gid in enumerate(data.option_group_ids):
            db.add(ProductOptionGroup(product_id=product.id, group_id=gid, sort_order=idx))
    await db.flush()
    return success({"id": product.id}, request=request)


@router.get("/options/groups")
async def list_option_groups(request: Request, db: DbSession):
    result = await db.execute(select(OptionGroup).where(OptionGroup.status == 1).order_by(OptionGroup.sort_order))
    groups = []
    for g in result.scalars():
        opts = await db.execute(select(Option).where(Option.group_id == g.id, Option.status == 1).order_by(Option.sort_order))
        groups.append({
            "id": g.id, "name": g.name, "code": g.code, "selection_type": g.selection_type,
            "is_required": g.is_required,
            "options": [{"id": o.id, "name": o.name, "code": o.code, "extra_price": str(o.extra_price)} for o in opts.scalars()],
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
