"""跨门店菜单复制。"""
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import Option, OptionGroup, Product, ProductCategory, ProductOptionGroup, Store
from app.services.menu_copy_service import MenuCopyService


@pytest_asyncio.fixture
async def two_stores(db_session: AsyncSession):
    a = Store(id=701, store_no="A01", name="源店", status=1)
    b = Store(id=702, store_no="B01", name="目标店", status=1)
    db_session.add_all([a, b])
    await db_session.flush()
    return a, b


@pytest.mark.asyncio
async def test_copy_categories_and_products(db_session: AsyncSession, two_stores):
    src, dst = two_stores
    cat = ProductCategory(id=711, category_no="A01-Mian", store_id=src.id, name="面", sort_order=1, status=1)
    db_session.add(cat)
    await db_session.flush()
    shared = OptionGroup(
        id=721,
        store_id=None,
        name="辣",
        code="0000-La",
        selection_type="SINGLE",
        is_required=False,
        sort_order=0,
        status=1,
    )
    private = OptionGroup(
        id=722,
        store_id=src.id,
        name="店特调",
        code="A01-ShopSpec",
        selection_type="SINGLE",
        is_required=False,
        sort_order=1,
        status=1,
    )
    db_session.add_all([shared, private])
    await db_session.flush()
    db_session.add(
        Option(
            id=731,
            group_id=private.id,
            name="特A",
            code="A01-ShopSpec-A",
            extra_price=0,
            sort_order=0,
            status=1,
        )
    )
    prod = Product(
        id=741,
        store_id=src.id,
        category_id=cat.id,
        product_no="A01-Mian-N001",
        name="牛肉面",
        price=Decimal("18.00"),
        status="ON_SALE",
        stock=99,
    )
    db_session.add(prod)
    await db_session.flush()
    db_session.add(ProductOptionGroup(id=751, product_id=prod.id, group_id=shared.id, sort_order=0))
    db_session.add(ProductOptionGroup(id=752, product_id=prod.id, group_id=private.id, sort_order=1))
    await db_session.flush()

    result = await MenuCopyService.copy_to_store(
        db_session,
        target_store_id=int(dst.id),
        product_ids=[int(prod.id)],
        on_product_conflict="skip",
    )
    assert result["categories"]["created"] == 1
    assert result["option_groups"]["created"] == 1
    assert result["option_groups"]["skipped_shared"] == 1
    assert result["products"]["created"] == 1

    dst_cat = (
        await db_session.execute(
            select(ProductCategory).where(ProductCategory.store_id == dst.id, ProductCategory.name == "面")
        )
    ).scalar_one()
    assert dst_cat.category_no == "B01-Mian"
    dst_prod = (
        await db_session.execute(
            select(Product).where(Product.store_id == dst.id, Product.product_no == "B01-Mian-N001")
        )
    ).scalar_one()
    assert int(dst_prod.category_id) == int(dst_cat.id)
    links = list(
        (
            await db_session.execute(
                select(ProductOptionGroup).where(ProductOptionGroup.product_id == dst_prod.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(links) == 2
    gids = {int(x.group_id) for x in links}
    assert int(shared.id) in gids
    assert int(private.id) not in gids


@pytest.mark.asyncio
async def test_product_conflict_skip_and_rename(db_session: AsyncSession, two_stores):
    src, dst = two_stores
    src_cat = ProductCategory(id=812, category_no="A01-Mian", store_id=src.id, name="面", sort_order=0, status=1)
    dst_cat = ProductCategory(id=813, category_no="B01-Mian", store_id=dst.id, name="面", sort_order=0, status=1)
    db_session.add_all([src_cat, dst_cat])
    await db_session.flush()
    src_prod = Product(
        id=821,
        store_id=src.id,
        category_id=src_cat.id,
        product_no="A01-Mian-SAME",
        name="同编号",
        price=Decimal("10.00"),
        status="ON_SALE",
    )
    dst_prod = Product(
        id=822,
        store_id=dst.id,
        category_id=dst_cat.id,
        product_no="B01-Mian-SAME",
        name="同编号",
        price=Decimal("10.00"),
        status="ON_SALE",
    )
    db_session.add_all([src_prod, dst_prod])
    await db_session.flush()

    skip_res = await MenuCopyService.copy_to_store(
        db_session,
        target_store_id=int(dst.id),
        product_ids=[int(src_prod.id)],
        on_product_conflict="skip",
    )
    assert skip_res["products"]["skipped"] == 1
    assert skip_res["products"]["created"] == 0

    rename_res = await MenuCopyService.copy_to_store(
        db_session,
        target_store_id=int(dst.id),
        product_ids=[int(src_prod.id)],
        on_product_conflict="rename",
    )
    assert rename_res["products"]["created"] == 1
    assert rename_res["products"]["renamed"] == 1
    renamed = (
        await db_session.execute(
            select(Product).where(Product.store_id == dst.id, Product.product_no == "B01-Mian-SAMEC1")
        )
    ).scalar_one_or_none()
    assert renamed is not None


@pytest.mark.asyncio
async def test_empty_selection_raises(db_session: AsyncSession, two_stores):
    _, dst = two_stores
    with pytest.raises(AppException) as ei:
        await MenuCopyService.copy_to_store(db_session, target_store_id=int(dst.id))
    assert ei.value.code == 40001


@pytest.mark.asyncio
async def test_clone_shared_option_groups(db_session: AsyncSession, two_stores):
    src, dst = two_stores
    # 目标店编号用于编码前缀
    dst.store_no = "SH01"
    await db_session.flush()
    shared = OptionGroup(
        id=961,
        store_id=None,
        name="量",
        code="0000-Liang",
        selection_type="SINGLE",
        is_required=False,
        sort_order=0,
        status=1,
    )
    db_session.add(shared)
    await db_session.flush()
    db_session.add(
        Option(
            id=962,
            group_id=shared.id,
            name="小碗",
            code="0000-Liang-XiaoWan",
            extra_price=0,
            sort_order=0,
            status=1,
        )
    )
    await db_session.flush()

    result = await MenuCopyService.copy_to_store(
        db_session,
        target_store_id=int(dst.id),
        option_group_ids=[int(shared.id)],
        clone_shared_option_groups=True,
    )
    assert result["option_groups"]["created"] == 1
    assert result["option_groups"]["cloned_shared"] == 1
    cloned = (
        await db_session.execute(
            select(OptionGroup).where(OptionGroup.store_id == dst.id, OptionGroup.code == "SH01-Liang")
        )
    ).scalar_one()
    assert int(cloned.id) != int(shared.id)
    opts = list(
        (await db_session.execute(select(Option).where(Option.group_id == cloned.id))).scalars().all()
    )
    assert len(opts) == 1
    assert opts[0].code == "SH01-Liang-XiaoWan"
    assert opts[0].name == "小碗"

@pytest.mark.asyncio
async def test_reuse_same_name_category(db_session: AsyncSession, two_stores):
    src, dst = two_stores
    src_cat = ProductCategory(id=911, category_no="C911", store_id=src.id, name="面", sort_order=1, status=1)
    dst_cat = ProductCategory(id=912, category_no="C912", store_id=dst.id, name="面", sort_order=9, status=1)
    db_session.add_all([src_cat, dst_cat])
    await db_session.flush()

    result = await MenuCopyService.copy_to_store(
        db_session,
        target_store_id=int(dst.id),
        category_ids=[int(src_cat.id)],
    )
    assert result["categories"]["reused"] == 1
    assert result["categories"]["created"] == 0
    assert result["category_id_map"][str(src_cat.id)] == dst_cat.id
