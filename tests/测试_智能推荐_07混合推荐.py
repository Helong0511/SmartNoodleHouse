"""Task 7 验收：商品池过滤、三态分支、四路打分、分类权重翻转、负反馈降权、
去重窗口、settle 搭配排除、多样性、共现 CF、reason 非空。"""
import pytest

from app.models import CustomerTasteProfile, ProductTasteProfile, StoreProductStock
from app.services.recommendation_service import (
    ProfilePoint,
    RecommendationService,
    customer_state,
    dedup_window_days,
    rank_candidates,
)
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
    utcnow,
)


async def _taste(db, product, *, tags=None, dims=None):
    row = ProductTasteProfile(
        product_id=product.id,
        store_id=product.store_id,
        tags=tags or [],
        dimensions=dims or {},
        source="AUTO",
        derived_from="test",
    )
    db.add(row)
    await db.flush()
    return row


async def _profile(db, customer_id, dim_type, key, score, event_count, target_name=None):
    db.add(CustomerTasteProfile(
        customer_id=customer_id,
        dim_type=dim_type,
        target_key=key,
        score=score,
        raw_weight=score,
        event_count=event_count,
        target_name=target_name,
        last_event_at=utcnow(),
        last_computed_at=utcnow(),
    ))
    await db.flush()


def _ids(items):
    return [r.product.id for r in items]


# ---------- 纯函数小测 ----------


def test_customer_state_three_tiers():
    assert customer_state(0, True) == "COLD"
    assert customer_state(3, True) == "WARM"
    assert customer_state(5, True) == "HOT"
    assert customer_state(99, False) == "COLD"


def test_dedup_window_by_category_and_config():
    assert dedup_window_days("招牌饮品", None) == 1
    assert dedup_window_days("重庆小面", None) == 7
    assert dedup_window_days("风味小食", None) == 3
    assert dedup_window_days("招牌饮品", {"dedup_window_days": 14}) == 14


# ---------- IO：商品池 ----------


@pytest.mark.asyncio
async def test_pool_cross_store_and_off_sale_and_stock_filtered(db_session):
    db = db_session
    s1 = await make_store(db)
    s2 = await make_store(db)
    c1 = await make_category(db, s1.id, name="面")
    c2 = await make_category(db, s2.id, name="面")
    p_ok = await make_product(db, s1.id, c1.id, name="正常在售")
    await make_product(db, s2.id, c2.id, name="别的店的面")
    await make_product(db, s1.id, c1.id, name="停售品", status="OFF_SHELF")
    p_stopped = await make_product(db, s1.id, c1.id, name="本店停售覆盖")
    p_zero = await make_product(db, s1.id, c1.id, name="库存为零", stock=0)
    db.add(StoreProductStock(id=910001, store_id=s1.id, product_id=p_stopped.id, stock=99, status=0))
    db.add(StoreProductStock(id=910002, store_id=s1.id, product_id=p_ok.id, stock=3, status=1))
    await db.flush()

    pool, _ = await RecommendationService.load_pool(db, s1.id, use_cache=False)
    ids = {p.id for p in pool}
    assert ids == {p_ok.id}
    assert pool[0].stock == 3  # 门店覆盖库存生效


@pytest.mark.asyncio
async def test_empty_store_returns_empty(db_session):
    db = db_session
    s = await make_store(db)
    items, state, meta = await RecommendationService.recommend(db, s.id, use_cache=False)
    assert items == []
    assert state == "COLD"
    assert meta["pool_size"] == 0


# ---------- 三态 ----------


@pytest.mark.asyncio
async def test_cold_customer_prefers_hot_and_novel(db_session):
    db = db_session
    s = await make_store(db)
    c = await make_category(db, s.id, name="面")
    p_hot = await make_product(db, s.id, c.id, name="招牌热卖", sales_count=200, is_hot=True)
    p_rec = await make_product(db, s.id, c.id, name="店长新品", sales_count=0, is_recommended=True)
    await make_product(db, s.id, c.id, name="普通款", sales_count=1)
    cust = await make_customer(db)

    items, state, meta = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=3, use_cache=False
    )
    assert state == "COLD"
    assert meta["pool_size"] == 3
    ids = _ids(items)
    assert ids[0] == p_hot.id
    assert p_rec.id in ids
    assert all(r.reason for r in items)
    assert set(r.reason for r in items) <= {"本店热销", "店长推荐", "新品上市", "为您推荐"}


@pytest.mark.asyncio
async def test_hot_customer_content_matches_spicy_profile(db_session):
    db = db_session
    s = await make_store(db)
    cat_m = await make_category(db, s.id, name="招牌面")
    cat_d = await make_category(db, s.id, name="饮品")
    p_spicy = await make_product(db, s.id, cat_m.id, name="麻辣小面")
    p_plain = await make_product(db, s.id, cat_m.id, name="清汤素面")
    p_tea = await make_product(db, s.id, cat_d.id, name="冰镇柠檬茶")
    await _taste(db, p_spicy, tags=["麻辣", "劲道"], dims={"spicy": 2, "numbing": 2})
    await _taste(db, p_tea, tags=["冰饮"], dims={"temperature": 0})
    cust = await make_customer(db)
    await _profile(db, cust.id, "CATEGORY", str(cat_m.id), 0.9, 6, target_name="招牌面")
    await _profile(db, cust.id, "TASTE_TAG", "tag:麻辣", 0.92, 6)
    await _profile(db, cust.id, "TASTE_TAG", "dim:spicy=2", 0.85, 5)

    items, state, _ = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=3, use_cache=False
    )
    assert state == "HOT"
    assert _ids(items)[0] == p_spicy.id
    top = items[0]
    assert "辣" in top.reason or "麻" in top.reason
    assert top.scores["content"] > top.scores["popular"]
    assert p_plain.id in _ids(items) and p_tea.id in _ids(items)


# ---------- 分类配置权重翻转（TR-7.2） ----------


@pytest.mark.asyncio
async def test_category_config_flips_order(db_session):
    db = db_session
    s = await make_store(db)
    cat_pref = await make_category(db, s.id, name="定制面")
    cat_pop = await make_category(db, s.id, name="大众面")
    p_pref = await make_product(db, s.id, cat_pref.id, name="我的专属口味面", sales_count=0)
    p_pop = await make_product(db, s.id, cat_pop.id, name="大众爆款面", sales_count=300, is_hot=True)
    await _taste(db, p_pref, tags=["麻辣"], dims={"spicy": 2})
    cust = await make_customer(db)
    await _profile(db, cust.id, "CATEGORY", str(cat_pref.id), 0.95, 4, target_name="定制面")
    await _profile(db, cust.id, "TASTE_TAG", "tag:麻辣", 0.9, 4)

    pool, cat_map = await RecommendationService.load_pool(db, s.id, use_cache=False)
    profiles, _ = await RecommendationService.load_profiles(db, cust.id)

    def rank(configs):
        return rank_candidates(
            pool,
            profiles=profiles,
            cf_scores={},
            recent_purchases={},
            configs_by_category=configs,
            state="HOT",
            limit=2,
        )

    # 默认权重：个性化内容品在前
    default_cfg = {c.id: c.recommend_config for c in cat_map.values()}
    assert _ids(rank(default_cfg))[0] == p_pref.id

    # 偏好面分类改为只看热销 → 爆款反超
    cat_pref.recommend_config = {"weights": {"content": 0.0, "cf": 0.0, "popular": 1.0, "novel": 0.0}}
    flipped = {c.id: c.recommend_config for c in cat_map.values()}
    assert _ids(rank(flipped))[0] == p_pop.id

    # 删除配置回退默认 → 个性化品重新在前
    cat_pref.recommend_config = None
    reset_cfg = {c.id: c.recommend_config for c in cat_map.values()}
    assert _ids(rank(reset_cfg))[0] == p_pref.id


# ---------- 负反馈 / 去重 / settle / 多样性 ----------


@pytest.mark.asyncio
async def test_negative_product_demoted(db_session):
    db = db_session
    s = await make_store(db)
    c = await make_category(db, s.id, name="饮品")
    p_bad = await make_product(db, s.id, c.id, name="被差评柠檬茶", sales_count=10)
    p_good = await make_product(db, s.id, c.id, name="普通柠檬茶", sales_count=10)
    cust = await make_customer(db)
    await _profile(db, cust.id, "PRODUCT", str(p_bad.id), 0.15, 2)
    await _profile(db, cust.id, "CATEGORY", str(c.id), 0.6, 4, target_name="饮品")

    items, state, _ = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=2, use_cache=False
    )
    assert state == "HOT"
    assert _ids(items)[0] == p_good.id
    bad_item = next(r for r in items if r.product.id == p_bad.id)
    assert bad_item.reason != "您常点的"


@pytest.mark.asyncio
async def test_repurchase_window_filters_when_pool_sufficient(db_session):
    db = db_session
    s = await make_store(db)
    cat_n = await make_category(db, s.id, name="招牌面")  # 7 天窗口
    cat_d = await make_category(db, s.id, name="饮品")    # 1 天窗口
    p_bought = await make_product(db, s.id, cat_n.id, name="前天吃的面", sales_count=50, is_hot=True)
    for i in range(3):
        await make_product(db, s.id, cat_d.id, name=f"饮品{i}", sales_count=20, is_hot=True)
    cust = await make_customer(db)
    # 2 天前买过面（面窗口 7 天内）；真实链路支付即学习，画像处于 WARM
    await make_paid_order(db, cust.id, s.id, [(p_bought, 1, [])], days_ago=2)
    await _profile(db, cust.id, "CATEGORY", str(cat_n.id), 0.7, 2, target_name="招牌面")

    items, state, _ = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=3, use_cache=False
    )
    assert state == "WARM"
    assert p_bought.id not in _ids(items)


@pytest.mark.asyncio
async def test_settle_addon_excludes_cart_and_favors_other_categories(db_session):
    db = db_session
    s = await make_store(db)
    cat_n = await make_category(db, s.id, name="招牌面")
    cat_d = await make_category(db, s.id, name="饮品")
    p_cart_noodle = await make_product(db, s.id, cat_n.id, name="购物车的面", sales_count=100, is_hot=True)
    p_noodle2 = await make_product(db, s.id, cat_n.id, name="另一款面", sales_count=15)
    p_drink = await make_product(db, s.id, cat_d.id, name="搭配饮品", sales_count=12)
    cust = await make_customer(db)

    items, _, _ = await RecommendationService.recommend(
        db,
        s.id,
        customer_id=cust.id,
        scene="settle_addon",
        limit=2,
        exclude_ids=[p_cart_noodle.id],
        use_cache=False,
    )
    ids = _ids(items)
    assert p_cart_noodle.id not in ids
    # 跨品类搭配加权：饮品压过更高销量的同品类面
    assert ids[0] == p_drink.id
    assert items[0].reason == "搭配加点"
    assert p_noodle2.id in ids


@pytest.mark.asyncio
async def test_diversity_no_three_same_category_in_a_row(db_session):
    db = db_session
    s = await make_store(db)
    cat_a = await make_category(db, s.id, name="面类")
    cat_b = await make_category(db, s.id, name="饮品")
    cat_c = await make_category(db, s.id, name="小食")
    for i in range(4):
        await make_product(db, s.id, cat_a.id, name=f"面{i}", sales_count=100 - i, is_hot=True)
    await make_product(db, s.id, cat_b.id, name="饮品", sales_count=10)
    await make_product(db, s.id, cat_c.id, name="小食", sales_count=10)
    cust = await make_customer(db)

    items, _, _ = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=6, use_cache=False
    )
    cats = [r.product.category_id for r in items]
    for i in range(len(cats) - 2):
        assert not (cats[i] == cats[i + 1] == cats[i + 2]), f"同分类三连: {cats}"


# ---------- 共现 CF ----------


@pytest.mark.asyncio
async def test_cf_lifts_cooccurring_product(db_session):
    db = db_session
    s = await make_store(db)
    cat = await make_category(db, s.id, name="招牌面")
    p_seed = await make_product(db, s.id, cat.id, name="种子面")
    p_co = await make_product(db, s.id, cat.id, name="共现卤味搭档")
    p_other = await make_product(db, s.id, cat.id, name="毫无关系的面")
    # 种子顾客：HOT，PRODUCT 种子面高偏好（自己没在本店下过单，避免 recent 干扰）
    me = await make_customer(db)
    await _profile(db, me.id, "PRODUCT", str(p_seed.id), 0.85, 3)
    await _profile(db, me.id, "CATEGORY", str(cat.id), 0.7, 3, target_name="招牌面")
    # 其他顾客 3 单 seed + co 同单
    for i in range(3):
        other = await make_customer(db)
        await make_paid_order(db, other.id, s.id, [(p_seed, 1, []), (p_co, 1, [])], days_ago=i + 1)

    pool, _ = await RecommendationService.load_pool(db, s.id, use_cache=False)
    profiles, _ = await RecommendationService.load_profiles(db, me.id)
    co = await RecommendationService._load_cooccurrence(db, s.id, use_cache=False)
    cf = RecommendationService.cf_scores_from_seeds([p_seed.id], co, {p.id for p in pool})
    assert cf.get(p_co.id, 0) > 0
    assert p_other.id not in cf

    items, state, _ = await RecommendationService.recommend(
        db, s.id, customer_id=me.id, limit=3, use_cache=False
    )
    assert state == "HOT"
    ids = _ids(items)
    assert ids.index(p_co.id) < ids.index(p_other.id)


# ---------- opt_out ----------


@pytest.mark.asyncio
async def test_opt_out_forces_cold_state(db_session):
    db = db_session
    s = await make_store(db)
    c = await make_category(db, s.id, name="面")
    await make_product(db, s.id, c.id, name="热销面", sales_count=88, is_hot=True)
    cust = await make_customer(db, recommend_opt_in=0)
    await _profile(db, cust.id, "CATEGORY", str(c.id), 0.99, 20)
    items, state, _ = await RecommendationService.recommend(
        db, s.id, customer_id=cust.id, limit=1, use_cache=False
    )
    assert state == "COLD"
    assert all(r.state == "COLD" for r in items)
