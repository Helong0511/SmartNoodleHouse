"""WP-C2：活动 boost_product_ids 进推荐；活动结束后加权消失；lifecycle 夜批可筛。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models import Customer, MarketingCampaign, MemberLevel
from app.services.lifecycle_service import LifecycleService, compute_lifecycle_code
from app.services.recommendation_service import PoolProduct, RecommendationService, rank_candidates
from tests.reco_factories import make_category, make_product, make_store, utcnow


def _pool_item(pid, cat_id, name, sales=10):
    return PoolProduct(
        id=pid,
        name=name,
        price=Decimal("15"),
        main_image=None,
        category_id=cat_id,
        category_name="面",
        is_hot=False,
        is_recommended=False,
        sales_count=sales,
        created_at=utcnow() - timedelta(days=60),
        stock=99,
        tags=[],
        dimensions={},
    )


@pytest.mark.asyncio
async def test_campaign_boost_raises_target_then_disappears(db_session):
    db = db_session
    store = await make_store(db, name="加权测店")
    cat = await make_category(db, store.id, name="面")
    # 销量接近时 1.25 乘子足以翻转排名；差距过大则乘子不够（符合产品预期）
    p_boost = await make_product(db, store.id, cat.id, name="活动招牌面", sales_count=45)
    p_other = await make_product(
        db, store.id, cat.id, name="普通面", sales_count=50, is_hot=True
    )

    now = utcnow()
    camp = MarketingCampaign(
        id=7201,
        store_id=store.id,
        code="boost_test",
        name="加权测",
        campaign_type="MEMBER_DAY",
        status="RUNNING",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(days=1),
        boost_product_ids=[int(p_boost.id)],
    )
    db.add(camp)
    await db.flush()

    boost_ids = await RecommendationService.load_campaign_boost_ids(db, store.id)
    assert int(p_boost.id) in boost_ids

    pool = [
        _pool_item(int(p_boost.id), int(cat.id), "活动招牌面", sales=45),
        _pool_item(int(p_other.id), int(cat.id), "普通面", sales=50),
    ]
    cold_cfg = {int(cat.id): {"weights": {"content": 0, "cf": 0, "popular": 1.0, "novel": 0}}}
    without = rank_candidates(
        pool,
        profiles={},
        cf_scores={},
        recent_purchases={},
        configs_by_category=cold_cfg,
        state="COLD",
        limit=2,
        boost_product_ids=set(),
    )
    with_boost = rank_candidates(
        pool,
        profiles={},
        cf_scores={},
        recent_purchases={},
        configs_by_category=cold_cfg,
        state="COLD",
        limit=2,
        boost_product_ids={int(p_boost.id)},
    )
    assert without[0].product.id == int(p_other.id)
    assert with_boost[0].product.id == int(p_boost.id)

    camp.status = "ENDED"
    await db.flush()
    assert int(p_boost.id) not in await RecommendationService.load_campaign_boost_ids(
        db, store.id
    )


@pytest.mark.asyncio
async def test_lifecycle_refresh_and_filter_codes(db_session):
    db = db_session
    lv = MemberLevel(id=7101, name="普会", min_points=0, discount_rate=Decimal("1"))
    db.add(lv)
    await db.flush()
    now = utcnow().replace(tzinfo=None)
    active = Customer(
        id=7101,
        customer_no="LC-A",
        display_name="活跃客",
        member_status="MEMBER",
        member_level_id=lv.id,
        member_activated_at=now - timedelta(days=30),
        last_order_at=now - timedelta(days=3),
        status=1,
    )
    dormant = Customer(
        id=7102,
        customer_no="LC-D",
        display_name="沉睡客",
        member_status="MEMBER",
        member_level_id=lv.id,
        member_activated_at=now - timedelta(days=200),
        last_order_at=now - timedelta(days=100),
        status=1,
    )
    guest = Customer(
        id=7103,
        customer_no="LC-G",
        display_name="游客",
        member_status="GUEST",
        status=1,
    )
    db.add_all([active, dormant, guest])
    await db.flush()

    assert compute_lifecycle_code(active, now=now) == "ACTIVE"
    assert compute_lifecycle_code(dormant, now=now) == "DORMANT"
    assert compute_lifecycle_code(guest, now=now) == "GUEST"

    stats = await LifecycleService.refresh_all(db, batch_size=50, now=now)
    assert stats["updated"] >= 3
    payload = await LifecycleService.get_payload(db, int(dormant.id))
    assert payload["lifecycle_code"] == "DORMANT"
    assert payload["source"] == "snapshot"
    assert payload["silence_days"] >= 90
