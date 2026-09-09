"""购买依赖：分类默认 + 商品级优先（方案 A）"""
from decimal import Decimal

import pytest

from app.core.exceptions import AppException
from app.models import Product, ProductCategory, Store
from app.services.category_require import normalize_id_list, resolve_product_requires
from app.services.order_service import OrderPriceCalculator


def test_normalize_id_list():
    assert normalize_id_list(None) == []
    assert normalize_id_list([]) == []
    assert normalize_id_list([3, 1, 1, "2", 0, -1, "x"]) == [3, 1, 2]
    assert normalize_id_list("[5, 7]") == [5, 7]


def test_resolve_product_requires_inherit_and_override():
    class Cat:
        require_category_ids = [10]

    class Prod:
        require_category_ids = None
        require_product_ids = None

    cats, prods, inherited = resolve_product_requires(Prod(), Cat())
    assert cats == [10] and prods == [] and inherited is True

    class Custom:
        require_category_ids = []
        require_product_ids = [99]

    cats2, prods2, inherited2 = resolve_product_requires(Custom(), Cat())
    assert cats2 == [] and prods2 == [99] and inherited2 is False

    class Clear:
        require_category_ids = []
        require_product_ids = []

    cats3, prods3, inherited3 = resolve_product_requires(Clear(), Cat())
    assert cats3 == [] and prods3 == [] and inherited3 is False


def test_store_require_lists_inherit_vs_custom():
    """创建商品未传依赖时应 custom=False → NULL，才能继续继承分类变更。"""
    from app.api.v1.products import _store_require_lists

    assert _store_require_lists([10], [], custom=False) == (None, None)
    assert _store_require_lists([10], [], custom=True) == ([10], [])
    assert _store_require_lists([], [], custom=True) == ([], [])


@pytest.mark.asyncio
async def test_addon_inherits_category_require(db_session):
    store = Store(id=801, store_no="T-REQ-01", name="依赖测试店", status=1)
    noodle_cat = ProductCategory(id=802, store_id=store.id, name="面", sort_order=0, status=1)
    rice_cat = ProductCategory(id=803, store_id=store.id, name="绵阳米粉", sort_order=1, status=1)
    addon_cat = ProductCategory(
        id=804,
        store_id=store.id,
        name="加料",
        sort_order=2,
        status=1,
        require_category_ids=[802],
    )
    db_session.add_all([store, noodle_cat, rice_cat, addon_cat])
    await db_session.flush()

    noodle = Product(
        id=811,
        store_id=store.id,
        category_id=noodle_cat.id,
        product_no="N001",
        name="牛肉面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    rice = Product(
        id=812,
        store_id=store.id,
        category_id=rice_cat.id,
        product_no="R001",
        name="牛肉米粉",
        price=Decimal("16.00"),
        status="ON_SALE",
    )
    egg = Product(
        id=813,
        store_id=store.id,
        category_id=addon_cat.id,
        product_no="A001",
        name="卤蛋",
        price=Decimal("2.00"),
        status="ON_SALE",
        require_category_ids=None,
        require_product_ids=None,
    )
    db_session.add_all([noodle, rice, egg])
    await db_session.flush()

    with pytest.raises(AppException) as ei:
        await OrderPriceCalculator.calculate_items_batch(
            db_session,
            [{"product_id": egg.id, "quantity": 1, "option_ids": []}],
            store_id=store.id,
        )
    assert ei.value.code == 40008

    with pytest.raises(AppException):
        await OrderPriceCalculator.calculate_items_batch(
            db_session,
            [
                {"product_id": rice.id, "quantity": 1, "option_ids": []},
                {"product_id": egg.id, "quantity": 1, "option_ids": []},
            ],
            store_id=store.id,
        )

    rows = await OrderPriceCalculator.calculate_items_batch(
        db_session,
        [
            {"product_id": noodle.id, "quantity": 1, "option_ids": []},
            {"product_id": egg.id, "quantity": 2, "option_ids": []},
        ],
        store_id=store.id,
    )
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_product_require_specific_product(db_session):
    store = Store(id=821, store_no="T-REQ-02", name="指定商品店", status=1)
    noodle_cat = ProductCategory(id=822, store_id=store.id, name="面", sort_order=0, status=1)
    addon_cat = ProductCategory(
        id=823,
        store_id=store.id,
        name="加料",
        sort_order=1,
        status=1,
        require_category_ids=[822],
    )
    db_session.add_all([store, noodle_cat, addon_cat])
    await db_session.flush()

    beef = Product(
        id=831,
        store_id=store.id,
        category_id=noodle_cat.id,
        product_no="N-BEEF",
        name="牛肉面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    intestine = Product(
        id=832,
        store_id=store.id,
        category_id=noodle_cat.id,
        product_no="N-FEI",
        name="肥肠面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    # 商品级覆盖：只允许牛肉面，不继承「任意面」
    egg = Product(
        id=833,
        store_id=store.id,
        category_id=addon_cat.id,
        product_no="A-EGG",
        name="卤蛋",
        price=Decimal("2.00"),
        status="ON_SALE",
        require_category_ids=[],
        require_product_ids=[831],
    )
    db_session.add_all([beef, intestine, egg])
    await db_session.flush()

    with pytest.raises(AppException) as ei:
        await OrderPriceCalculator.calculate_items_batch(
            db_session,
            [
                {"product_id": intestine.id, "quantity": 1, "option_ids": []},
                {"product_id": egg.id, "quantity": 1, "option_ids": []},
            ],
            store_id=store.id,
        )
    assert ei.value.code == 40008
    assert "牛肉面" in ei.value.message

    rows = await OrderPriceCalculator.calculate_items_batch(
        db_session,
        [
            {"product_id": beef.id, "quantity": 1, "option_ids": []},
            {"product_id": egg.id, "quantity": 1, "option_ids": []},
        ],
        store_id=store.id,
    )
    assert len(rows) == 2
