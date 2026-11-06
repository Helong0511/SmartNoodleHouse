"""PreferenceService：持续按已付订单分析并修正智能口味。"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Customer,
    CustomerPreference,
    Order,
    OrderItem,
    OrderItemOption,
    Product,
    ProductCategory,
    Store,
)
from app.services.customer_service import PreferenceService


def test_preference_score_denominator_uses_payment_status():
    """分母按 payment_status 统计已支付订单，与 PAID 迁移写入偏好一致。"""
    assert PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES == ("PAID", "PARTIAL_REFUND")


def test_order_weight_weekend_and_partial_refund():
    from types import SimpleNamespace
    from datetime import datetime

    decay = PreferenceService.RECENCY_DECAY
    weekday = SimpleNamespace(
        paid_at=datetime(2026, 9, 1, 12, 0, 0),  # 周二午市
        payment_status="PAID",
    )
    weekend = SimpleNamespace(
        paid_at=datetime(2026, 9, 5, 12, 0, 0),  # 周六午市
        payment_status="PAID",
    )
    partial = SimpleNamespace(
        paid_at=datetime(2026, 9, 1, 12, 0, 0),
        payment_status="PARTIAL_REFUND",
    )
    w0 = PreferenceService._order_weight(weekday, idx=0, decay=decay)
    w_end = PreferenceService._order_weight(weekend, idx=0, decay=decay)
    w_part = PreferenceService._order_weight(partial, idx=0, decay=decay)
    assert w_end == w0 * PreferenceService.WEEKEND_BOOST
    assert w_part == w0 * PreferenceService.PARTIAL_REFUND_WEIGHT


def test_scene_settings_env_default_true():
    from app.services.system_settings_service import SystemSettingsService

    assert SystemSettingsService._env_default("preference_scene_enabled") == "true"
    assert SystemSettingsService._env_default("weather_pref_enabled") == "true"


def test_kitchen_statuses_not_used_for_preference_denominator():
    """厨房态不应作为偏好分母（订单离开 PAID 后 status 会变，但 payment_status 不变）。"""
    kitchen = ("CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED")
    for st in kitchen:
        assert st not in PreferenceService.PREFERENCE_SCORE_PAYMENT_STATUSES


def _patch_sqlite_pref_ids(db_session, start_id=920):
    next_id = start_id

    def _add_with_sqlite_ids(obj):
        nonlocal next_id
        if isinstance(obj, CustomerPreference) and getattr(obj, "id", None) is None:
            obj.id = next_id
            next_id += 1
        db_session.sync_session.add(obj)

    db_session.add = _add_with_sqlite_ids  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_update_from_order_scores_by_payment_status(db_session):
    """分母统计 payment_status=PAID，不受厨房态 status 影响；分数为近期加权占比。"""
    store = Store(id=901, store_no="T-PREF-01", name="偏好测试店", status=1)
    customer = Customer(id=902, customer_no="C-PREF-01", nickname="偏好测试", status=1, recommend_opt_in=1)
    cat = ProductCategory(id=903, category_no="C903", store_id=store.id, name="面类", sort_order=1, status=1)
    db_session.add_all([store, customer, cat])
    await db_session.flush()

    product = Product(
        id=904,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-PREF-01",
        name="测试面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    db_session.add(product)
    await db_session.flush()

    base = datetime(2026, 9, 1, 12, 0, 0)
    kitchen_order = Order(
        id=905,
        order_no="O-PREF-KITCHEN",
        customer_id=customer.id,
        store_id=store.id,
        status="COOKING",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=base,
        created_at=base,
    )
    paid_order = Order(
        id=906,
        order_no="O-PREF-PAID",
        customer_id=customer.id,
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=base + timedelta(hours=1),
        created_at=base + timedelta(hours=1),
    )
    db_session.add_all([kitchen_order, paid_order])
    await db_session.flush()

    # 厨房态旧单也挂规格（不同口味），验证 payment_status=PAID 仍计入分母
    item_old = OrderItem(
        id=907,
        order_id=kitchen_order.id,
        product_id=product.id,
        product_name_snapshot="测试面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    item = OrderItem(
        id=909,
        order_id=paid_order.id,
        product_id=product.id,
        product_name_snapshot="测试面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    db_session.add_all([item_old, item])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItemOption(
                id=908,
                order_item_id=item_old.id,
                group_name_snapshot="辣度",
                option_name_snapshot="特辣",
                option_code="HOT",
            ),
            OrderItemOption(
                id=910,
                order_item_id=item.id,
                group_name_snapshot="辣度",
                option_name_snapshot="微辣",
                option_code="MILD",
            ),
        ]
    )
    await db_session.commit()

    _patch_sqlite_pref_ids(db_session)

    await PreferenceService.update_from_order(db_session, paid_order.id)
    await db_session.commit()

    prefs = {
        p.option_code: p
        for p in (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    }
    assert set(prefs) == {"MILD", "HOT"}
    assert prefs["MILD"].order_count == 1
    # 两单均在午市，时段加权相消：最新 weight=1，旧单 weight=0.85 → 1/1.85
    expected = PreferenceService._score_quantize(Decimal("1") / (Decimal("1") + PreferenceService.RECENCY_DECAY))
    assert prefs["MILD"].preference_score == expected


@pytest.mark.asyncio
async def test_update_from_order_skips_non_paid_status(db_session):
    """仅在 status=PAID 时写入偏好，COMPLETED 等状态不重复写入。"""
    store = Store(id=911, store_no="T-PREF-02", name="偏好测试店2", status=1)
    customer = Customer(id=912, customer_no="C-PREF-02", nickname="偏好测试2", status=1, recommend_opt_in=1)
    db_session.add_all([store, customer])
    await db_session.flush()

    completed_order = Order(
        id=913,
        order_no="O-PREF-DONE",
        customer_id=customer.id,
        store_id=store.id,
        status="COMPLETED",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
    )
    db_session.add(completed_order)
    await db_session.commit()

    await PreferenceService.update_from_order(db_session, completed_order.id)
    await db_session.commit()

    prefs = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    )
    assert prefs == []


@pytest.mark.asyncio
async def test_same_option_in_one_order_counts_by_line_qty(db_session):
    """同一订单内同一规格按行数量加权（两行各 1 份计 2 次）。"""
    store = Store(id=941, store_no="T-PREF-DEDUP", name="去重店", status=1)
    customer = Customer(id=942, customer_no="C-PREF-DEDUP", nickname="去重客", status=1, recommend_opt_in=1)
    cat = ProductCategory(id=943, category_no="C943", store_id=store.id, name="面类", sort_order=1, status=1)
    product = Product(
        id=944,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-DEDUP",
        name="面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    order = Order(
        id=945,
        order_no="O-DEDUP",
        customer_id=customer.id,
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("36.00"),
        total_amount=Decimal("36.00"),
        paid_at=datetime(2026, 9, 3, 12, 0, 0),
    )
    db_session.add_all([store, customer, cat, product, order])
    await db_session.flush()
    item1 = OrderItem(
        id=946,
        order_id=order.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    item2 = OrderItem(
        id=947,
        order_id=order.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    db_session.add_all([item1, item2])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItemOption(
                id=948,
                order_item_id=item1.id,
                group_name_snapshot="辣度",
                option_name_snapshot="微辣",
                option_code="MILD",
            ),
            OrderItemOption(
                id=949,
                order_item_id=item2.id,
                group_name_snapshot="辣度",
                option_name_snapshot="微辣",
                option_code="MILD",
            ),
        ]
    )
    await db_session.commit()
    _patch_sqlite_pref_ids(db_session, start_id=950)

    await PreferenceService.update_from_order(db_session, order.id)
    await db_session.commit()

    prefs = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    )
    assert len(prefs) == 1
    assert prefs[0].order_count == 2
    assert prefs[0].preference_score == Decimal("1.0000")


@pytest.mark.asyncio
async def test_recent_order_corrects_taste_preference(db_session):
    """口味改变后，近期订单权重更高，新口味分数应高于旧口味。"""
    store = Store(id=961, store_no="T-PREF-SHIFT", name="口味变化店", status=1)
    customer = Customer(id=962, customer_no="C-PREF-SHIFT", nickname="变化客", status=1, recommend_opt_in=1)
    cat = ProductCategory(id=963, category_no="C963", store_id=store.id, name="面类", sort_order=1, status=1)
    product = Product(
        id=964,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-SHIFT",
        name="面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    db_session.add_all([store, customer, cat, product])
    await db_session.flush()

    base = datetime(2026, 9, 1, 10, 0, 0)
    old_order = Order(
        id=965,
        order_no="O-SHIFT-OLD",
        customer_id=customer.id,
        store_id=store.id,
        status="COMPLETED",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=base,
        created_at=base,
    )
    new_order = Order(
        id=966,
        order_no="O-SHIFT-NEW",
        customer_id=customer.id,
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=base + timedelta(days=3),
        created_at=base + timedelta(days=3),
    )
    db_session.add_all([old_order, new_order])
    await db_session.flush()

    old_item = OrderItem(
        id=967,
        order_id=old_order.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    new_item = OrderItem(
        id=968,
        order_id=new_order.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    db_session.add_all([old_item, new_item])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItemOption(
                id=969,
                order_item_id=old_item.id,
                group_name_snapshot="辣度",
                option_name_snapshot="微辣",
                option_code="MILD",
            ),
            OrderItemOption(
                id=970,
                order_item_id=new_item.id,
                group_name_snapshot="辣度",
                option_name_snapshot="特辣",
                option_code="HOT",
            ),
        ]
    )
    await db_session.commit()
    _patch_sqlite_pref_ids(db_session, start_id=980)

    await PreferenceService.update_from_order(db_session, new_order.id)
    await db_session.commit()

    prefs = {
        p.option_code: p
        for p in (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    }
    assert set(prefs) == {"MILD", "HOT"}
    assert prefs["HOT"].preference_score > prefs["MILD"].preference_score


@pytest.mark.asyncio
async def test_recompute_excludes_fully_refunded_orders(db_session):
    """全额退款订单退出分析，偏好随之修正。"""
    store = Store(id=981, store_no="T-PREF-REF", name="退款店", status=1)
    customer = Customer(id=982, customer_no="C-PREF-REF", nickname="退款客", status=1, recommend_opt_in=1)
    cat = ProductCategory(id=983, category_no="C983", store_id=store.id, name="面类", sort_order=1, status=1)
    product = Product(
        id=984,
        store_id=store.id,
        category_id=cat.id,
        product_no="P-REF",
        name="面",
        price=Decimal("18.00"),
        status="ON_SALE",
    )
    keep = Order(
        id=985,
        order_no="O-REF-KEEP",
        customer_id=customer.id,
        store_id=store.id,
        status="COMPLETED",
        payment_status="PAID",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=datetime(2026, 9, 2, 12, 0, 0),
    )
    refunded = Order(
        id=986,
        order_no="O-REF-GONE",
        customer_id=customer.id,
        store_id=store.id,
        status="REFUNDED",
        payment_status="REFUNDED",
        subtotal=Decimal("18.00"),
        total_amount=Decimal("18.00"),
        paid_at=datetime(2026, 9, 3, 12, 0, 0),
    )
    db_session.add_all([store, customer, cat, product, keep, refunded])
    await db_session.flush()
    keep_item = OrderItem(
        id=987,
        order_id=keep.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    refund_item = OrderItem(
        id=988,
        order_id=refunded.id,
        product_id=product.id,
        product_name_snapshot="面",
        unit_price_snapshot=Decimal("18.00"),
        quantity=1,
        subtotal=Decimal("18.00"),
    )
    db_session.add_all([keep_item, refund_item])
    await db_session.flush()
    db_session.add_all(
        [
            OrderItemOption(
                id=989,
                order_item_id=keep_item.id,
                group_name_snapshot="辣度",
                option_name_snapshot="微辣",
                option_code="MILD",
            ),
            OrderItemOption(
                id=990,
                order_item_id=refund_item.id,
                group_name_snapshot="辣度",
                option_name_snapshot="特辣",
                option_code="HOT",
            ),
        ]
    )
    await db_session.commit()
    _patch_sqlite_pref_ids(db_session, start_id=991)

    await PreferenceService.recompute_for_customer(db_session, customer.id)
    await db_session.commit()

    prefs = list(
        (
            await db_session.execute(
                select(CustomerPreference).where(CustomerPreference.customer_id == customer.id)
            )
        ).scalars()
    )
    assert len(prefs) == 1
    assert prefs[0].option_code == "MILD"
    assert prefs[0].preference_score == Decimal("1.0000")


@pytest.mark.asyncio
async def test_get_recommendations_includes_all_confirmed_prefs(db_session):
    """规格组 + 多项加料可超过 TOP_N=8，推荐列表不得截断末位规格（如蒜末）。"""
    customer = Customer(id=920, customer_no="C-PREF-MANY", nickname="多规格", status=1, recommend_opt_in=1)
    db_session.add(customer)
    await db_session.flush()

    rows = [
        ("量", "Liang-DaWAN", "大碗", 5),
        ("加料", "JiaLiao-FeiChang", "加肥肠", 5),
        ("加料", "JiaLiao-NiuRou", "加牛肉", 5),
        ("加料", "JiaLiao-LuDan", "加卤蛋", 5),
        ("辣", "La-WeiLa", "微辣", 5),
        ("麻", "Ma-WeiMa", "微麻", 5),
        ("葱", "Cong-ZhengChangCong", "正常葱", 5),
        ("香菜", "XiangCai-ZhengChangXiangCai", "正常香菜", 5),
        ("蒜末", "SuanMo-ZhengChangSuanMo", "正常蒜末", 5),
    ]
    for i, (cat, code, name, cnt) in enumerate(rows, start=1):
        db_session.add(
            CustomerPreference(
                id=930 + i,
                customer_id=customer.id,
                category=cat,
                option_code=code,
                option_name=name,
                preference_score=Decimal("0.50"),
                order_count=cnt,
            )
        )
    await db_session.commit()

    rec = await PreferenceService.get_recommendations(db_session, customer.id)
    codes = {p["option_code"] for p in rec["preferences"]}
    assert len(rec["preferences"]) == 9
    assert "SuanMo-ZhengChangSuanMo" in codes
