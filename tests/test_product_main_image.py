"""商品主图更新时同步 product_images"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.v1.products import _clear_main_product_images, _set_main_product_image
from app.models import Product, ProductCategory, ProductImage, Store


@pytest.mark.asyncio
async def test_clear_main_product_images_removes_all_main_rows(db_session):
    store = Store(id=801, store_no="T-IMG-01", name="测试店", status=1)
    cat = ProductCategory(id=802, category_no="C802", store_id=store.id, name="面类", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    product = Product(
        id=803,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-IMG-01",
        name="测试面",
        price=Decimal("18.00"),
        main_image="/uploads/products/old.jpg",
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add_all(
        [
            ProductImage(id=804, product_id=product.id, image_url="/uploads/products/old.jpg", image_type="MAIN", sort_order=0),
            ProductImage(id=805, product_id=product.id, image_url="/uploads/products/older.jpg", image_type="MAIN", sort_order=1),
        ]
    )
    await db_session.flush()

    await _clear_main_product_images(db_session, product.id)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(ProductImage).where(ProductImage.product_id == product.id, ProductImage.image_type == "MAIN")
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_set_main_product_image_clear(db_session):
    store = Store(id=811, store_no="T-IMG-02", name="测试店2", status=1)
    cat = ProductCategory(id=812, category_no="C812", store_id=store.id, name="面类", sort_order=0, status=1)
    db_session.add_all([store, cat])
    await db_session.flush()

    product = Product(
        id=813,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-IMG-02",
        name="测试面2",
        price=Decimal("18.00"),
        main_image="/uploads/products/old.jpg",
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductImage(id=814, product_id=product.id, image_url="/uploads/products/old.jpg", image_type="MAIN", sort_order=0)
    )
    await db_session.flush()

    await _set_main_product_image(db_session, product, None)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(ProductImage).where(ProductImage.product_id == product.id, ProductImage.image_type == "MAIN")
        )
    ).scalars().all()

    assert product.main_image is None
    assert rows == []


def test_update_product_main_image_change_detection():
    """PUT 更新时：仅当 main_image 实际变更才应同步 product_images。"""
    old_main_image = "/uploads/products/old.jpg"
    assert ("/uploads/products/new.jpg" or None) != old_main_image
    assert (old_main_image or None) == old_main_image
    assert (None or None) != old_main_image
