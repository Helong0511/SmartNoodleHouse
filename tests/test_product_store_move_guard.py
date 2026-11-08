from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.products import _ensure_product_store_move_safe
from app.core.exceptions import AppException
from app.models import Product, ProductCategory, Store


@pytest.mark.asyncio
async def test_cross_store_product_move_requires_explicit_target_category_and_options(
    db_session,
):
    source = Store(store_no="PROD-MOVE-A", name="餐品迁移源门店", status=1)
    target = Store(store_no="PROD-MOVE-B", name="餐品迁移目标门店", status=1)
    db_session.add_all([source, target])
    await db_session.flush()

    category = ProductCategory(
        store_id=int(source.id),
        category_no="PROD-MOVE-A-01",
        name="源分类",
        status=1,
    )
    db_session.add(category)
    await db_session.flush()

    product = Product(
        store_id=int(source.id),
        category_id=int(category.id),
        product_no="PROD-MOVE-P-01",
        name="待迁移餐品",
        price=Decimal("12.00"),
    )
    db_session.add(product)
    await db_session.flush()

    with pytest.raises(AppException, match="目标门店分类"):
        await _ensure_product_store_move_safe(
            db_session,
            product,
            int(target.id),
            provided_fields=set(),
            category_id=None,
            option_group_ids=None,
        )


@pytest.mark.asyncio
async def test_cross_store_product_move_allows_clean_product_with_explicit_mapping(
    db_session,
):
    source = Store(store_no="PROD-MOVE-C", name="餐品迁移源门店二", status=1)
    target = Store(store_no="PROD-MOVE-D", name="餐品迁移目标门店二", status=1)
    db_session.add_all([source, target])
    await db_session.flush()

    category = ProductCategory(
        store_id=int(source.id),
        category_no="PROD-MOVE-C-01",
        name="源分类二",
        status=1,
    )
    db_session.add(category)
    await db_session.flush()

    product = Product(
        store_id=int(source.id),
        category_id=int(category.id),
        product_no="PROD-MOVE-P-02",
        name="无历史餐品",
        price=Decimal("15.00"),
    )
    db_session.add(product)
    await db_session.flush()

    await _ensure_product_store_move_safe(
        db_session,
        product,
        int(target.id),
        provided_fields={"category_id", "option_group_ids"},
        category_id=99999,
        option_group_ids=[],
    )
