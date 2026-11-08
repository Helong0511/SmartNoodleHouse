from __future__ import annotations

from decimal import Decimal

import pytest

from app.api import products_categories
from app.core.exceptions import AppException
from app.models import Product, ProductCategory, Store


@pytest.mark.asyncio
async def test_category_cache_invalidation_clears_each_affected_store_once(
    db_session, monkeypatch
):
    local_invalidations: list[int] = []
    outbox_invalidations: list[int] = []

    async def fake_invalidate(_db, store_id):
        local_invalidations.append(int(store_id))
        outbox_invalidations.append(int(store_id))

    monkeypatch.setattr(
        products_categories,
        "invalidate_store_menu_cache",
        fake_invalidate,
    )

    await products_categories._invalidate_category_menu(
        db_session, 3, 2, 3, None, 2
    )

    assert local_invalidations == [2, 3]
    assert outbox_invalidations == [2, 3]



@pytest.mark.asyncio
async def test_shared_menu_invalidation_clears_local_cache_and_enqueues_outbox(
    db_session, monkeypatch
):
    from app.services import menu_cache_service, recommendation_service

    local_invalidations: list[int] = []
    outbox_invalidations: list[int] = []

    monkeypatch.setattr(
        recommendation_service,
        "invalidate_store_local_caches",
        lambda store_id: local_invalidations.append(int(store_id)),
    )

    async def fake_enqueue(_db, store_id):
        outbox_invalidations.append(int(store_id))

    monkeypatch.setattr(
        menu_cache_service,
        "enqueue_menu_cache_invalidation",
        fake_enqueue,
    )

    await menu_cache_service.invalidate_store_menu_cache(db_session, 9)
    await menu_cache_service.invalidate_store_menu_cache(db_session, None)

    assert local_invalidations == [9]
    assert outbox_invalidations == [9]

@pytest.mark.asyncio
async def test_category_store_move_is_rejected_when_products_are_still_linked(
    db_session,
):
    source = Store(store_no="CAT-MOVE-A", name="分类迁移源门店", status=1)
    target = Store(store_no="CAT-MOVE-B", name="分类迁移目标门店", status=1)
    db_session.add_all([source, target])
    await db_session.flush()

    category = ProductCategory(
        store_id=int(source.id),
        category_no="CAT-MOVE-A-01",
        name="仍有关联餐品的分类",
        status=1,
    )
    db_session.add(category)
    await db_session.flush()

    product = Product(
        store_id=int(source.id),
        category_id=int(category.id),
        product_no="CAT-MOVE-P-01",
        name="关联餐品",
        price=Decimal("12.00"),
    )
    db_session.add(product)
    await db_session.flush()

    with pytest.raises(AppException, match="仍关联餐品"):
        await products_categories._prepare_category_store_move(
            db_session, category, int(target.id)
        )
