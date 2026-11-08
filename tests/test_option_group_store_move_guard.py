from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.products_options import _ensure_option_group_store_move_safe
from app.core.exceptions import AppException
from app.models import OptionGroup, Product, ProductCategory, ProductOptionGroup, Store


@pytest.mark.asyncio
async def test_option_group_move_is_rejected_while_products_are_linked(db_session):
    source = Store(store_no="OPT-MOVE-A", name="规格迁移源门店", status=1)
    target = Store(store_no="OPT-MOVE-B", name="规格迁移目标门店", status=1)
    db_session.add_all([source, target])
    await db_session.flush()

    category = ProductCategory(
        store_id=int(source.id),
        category_no="OPT-MOVE-A-01",
        name="规格迁移分类",
        status=1,
    )
    db_session.add(category)
    await db_session.flush()

    product = Product(
        store_id=int(source.id),
        category_id=int(category.id),
        product_no="OPT-MOVE-P-01",
        name="绑定规格的餐品",
        price=Decimal("10.00"),
    )
    group = OptionGroup(
        store_id=int(source.id),
        name="面量",
        code="OPT-MOVE-A-MIANLIANG",
        selection_type="SINGLE",
        status=1,
    )
    db_session.add_all([product, group])
    await db_session.flush()
    db_session.add(
        ProductOptionGroup(
            product_id=int(product.id),
            group_id=int(group.id),
            sort_order=0,
        )
    )
    await db_session.flush()

    with pytest.raises(AppException, match="仍绑定餐品"):
        await _ensure_option_group_store_move_safe(
            db_session, group, int(target.id)
        )
