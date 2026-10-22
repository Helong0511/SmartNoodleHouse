"""总部∪门店并存：顾客端去重（储值档 / 积分兑券）与分类映射 resolve。"""
from decimal import Decimal
from types import SimpleNamespace

from app.api.miniprogram_customer import _merge_point_exchange_for_customer
from app.api.wallet import _merge_packages_for_customer
from app.services.product_sync_service import ProductSyncService
from app.services.scope_override import merge_store_over_hq, resolve_map_by_key


def _pkg(*, id: int, store_id, name: str, pay: str, gift: str = "0", sort: int = 0):
    return SimpleNamespace(
        id=id,
        store_id=store_id,
        name=name,
        pay_amount=Decimal(pay),
        gift_amount=Decimal(gift),
        sort_order=sort,
        status=1,
    )


def test_scope_override_helper_prefers_store():
    rows = [
        SimpleNamespace(id=1, store_id=None, key="a"),
        SimpleNamespace(id=2, store_id=1, key="a"),
        SimpleNamespace(id=3, store_id=None, key="b"),
    ]
    merged = merge_store_over_hq(rows, key_fn=lambda r: r.key, sort_key=lambda r: r.id)
    assert [r.id for r in merged] == [2, 3]
    resolved = resolve_map_by_key(rows, key_fn=lambda r: r.key)
    assert resolved["a"].id == 2
    assert resolved["b"].id == 3


def test_customer_merge_prefers_store_over_hq_same_pay():
    hq50 = _pkg(id=1, store_id=None, name="充50得55", pay="50.00", gift="5.00", sort=0)
    store50 = _pkg(id=10, store_id=1, name="充50得55", pay="50.00", gift="5.00", sort=0)
    hq100 = _pkg(id=2, store_id=None, name="充100得120", pay="100.00", gift="20.00", sort=1)
    hq200 = _pkg(id=3, store_id=None, name="充200得250", pay="200.00", gift="50.00", sort=2)

    merged = _merge_packages_for_customer([hq50, store50, hq100, hq200])
    assert [p.id for p in merged] == [10, 2, 3]
    assert 1 not in {p.id for p in merged}


def test_customer_merge_keeps_store_only_extra_tier():
    hq50 = _pkg(id=1, store_id=None, name="充50得55", pay="50.00", gift="5.00", sort=0)
    store80 = _pkg(id=11, store_id=1, name="充80得100", pay="80.00", gift="20.00", sort=5)

    merged = _merge_packages_for_customer([hq50, store80])
    assert [p.id for p in merged] == [1, 11]


def test_customer_merge_store_can_change_gift_same_pay():
    hq50 = _pkg(id=1, store_id=None, name="充50得55", pay="50.00", gift="5.00", sort=0)
    store50 = _pkg(id=12, store_id=2, name="本店充50得60", pay="50.00", gift="10.00", sort=0)

    merged = _merge_packages_for_customer([hq50, store50])
    assert len(merged) == 1
    assert merged[0].id == 12
    assert merged[0].gift_amount == Decimal("10.00")


def test_point_exchange_merge_prefers_store_same_template_cost():
    hq = SimpleNamespace(id=1, store_id=None, template_id=9, points_cost=100, sort_order=0, name="兑10元券")
    store = SimpleNamespace(id=2, store_id=1, template_id=9, points_cost=100, sort_order=0, name="本店兑10元券")
    extra = SimpleNamespace(id=3, store_id=None, template_id=10, points_cost=200, sort_order=1, name="兑20元券")

    merged = _merge_point_exchange_for_customer([hq, store, extra])
    assert [o.id for o in merged] == [2, 3]


def test_category_map_payload_prefers_store_platform_name():
    product = SimpleNamespace(
        product_no="P1",
        name="牛肉面",
        description="",
        price=Decimal("18.00"),
        stock=999,
        main_image="",
        box_price=0,
        min_order_count=1,
        category_id=7,
    )
    cat = SimpleNamespace(name="面类")
    hq_map = SimpleNamespace(
        store_id=None,
        local_category_id=7,
        platform_category_name="总部面食",
        platform_category_code="HQ-NOODLE",
        status=1,
    )
    store_map = SimpleNamespace(
        store_id=1,
        local_category_id=7,
        platform_category_name="本店招牌面",
        platform_category_code="ST-NOODLE",
        status=1,
    )
    resolved = resolve_map_by_key([hq_map, store_map], key_fn=lambda r: int(r.local_category_id))
    payload = ProductSyncService._build_product_payload_cached(
        product, cat, "shop-1", resolved[7]
    )
    assert payload["category_name"] == "本店招牌面"
    assert payload["platform_category_code"] == "ST-NOODLE"


def test_category_map_payload_falls_back_to_local_name():
    product = SimpleNamespace(
        product_no="P2",
        name="凉面",
        description="",
        price=Decimal("12.00"),
        stock=10,
        main_image="",
        box_price=0,
        min_order_count=1,
        category_id=8,
    )
    cat = SimpleNamespace(name="凉菜")
    payload = ProductSyncService._build_product_payload_cached(product, cat, "shop-1", None)
    assert payload["category_name"] == "凉菜"
    assert "platform_category_code" not in payload
