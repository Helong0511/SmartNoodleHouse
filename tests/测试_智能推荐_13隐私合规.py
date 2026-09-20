"""Task 13 验收：隐私合规 6 子项——
① opt_out 后事件不落库 + 推荐走 COLD 非个性化
② 画像查看完整（dimensions + spec_preferences）
③ 清除后行为事件/画像/评价/customer_preferences 为空 + 订单本体保留 + 审计有记录
④ 超期行为事件被定时任务清理
⑤ consent 版本/时间/opt_in 落库
⑥ A 令牌无法操作 B 的画像/清除（CurrentCustomer 鉴权天然隔离）
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api.v1.miniprogram import (
    clear_my_taste_profile,
    get_my_privacy_settings,
    get_my_taste_profile,
    record_privacy_consent,
    PrivacyConsentRequest,
    PrivacySettingsPatch,
    update_my_privacy_settings,
)
from app.models import (
    CustomerBehaviorEvent,
    CustomerPreference,
    CustomerTasteProfile,
    OperationLog,
    Order,
    ProductFeedback,
)
from app.services.behavior_event_service import ingest_events
from app.services.privacy_service import PrivacyService
from app.services.recommendation_service import RecommendationService
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
    utcnow,
)


def _ev(eid, etype, **kw):
    return {"event_id": eid, "event_type": etype, **kw}


async def _call(handler, db, customer, *, payload=None):
    """统一调用路由：handler(request=None, db=db, customer=customer, ...)。
    路由不接收 customer_id 参数，CurrentCustomer 从令牌解析，测试中直接传 customer 实例。"""
    if payload is not None:
        return await handler(payload, request=None, db=db, customer=customer)
    return await handler(request=None, db=db, customer=customer)


# ---------- TR-13.1 ① opt_out 后事件不落库 + 推荐走 COLD ----------

@pytest.mark.asyncio
async def test_opt_out_drops_events_and_non_personalized(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name="麻辣小面", sales_count=200, is_hot=True)
    c = await make_customer(db, recommend_opt_in=1)

    # 关闭个性化推荐
    resp = await _call(
        update_my_privacy_settings,
        db,
        c,
        payload=PrivacySettingsPatch(recommend_opt_in=0),
    )
    await db.commit()
    assert resp["data"]["recommend_opt_in"] == 0

    # 上报事件 → 全部丢弃
    r = await ingest_events(db, c, [
        _ev("e1", "CLICK", product_id=p.id, store_id=store.id, scene="menu"),
        _ev("e2", "CART_ADD", product_id=p.id, store_id=store.id),
    ])
    assert r["accepted"] == 0
    assert r["dropped"] == 2
    assert r["reason"] == "opt_out"
    total = (await db.execute(select(func.count()).select_from(CustomerBehaviorEvent))).scalar_one()
    assert total == 0

    # 推荐：opt_out 走 COLD（非个性化）
    ranked, state, _ = await RecommendationService.rank_candidates(
        db, store_id=store.id, customer_id=c.id, scene="recommend_menu", limit=10, use_cache=False
    )
    assert state == "COLD"
    assert ranked, "COLD 仍应返回热销兜底"


# ---------- TR-13.1 ② 画像查看完整 ----------

@pytest.mark.asyncio
async def test_taste_profile_view_contains_dimensions_and_prefs(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name="麻辣小面")
    c = await make_customer(db)

    # 建画像（CATEGORY + PRODUCT + TASTE_TAG 三维）
    db.add_all([
        CustomerTasteProfile(
            customer_id=c.id, dim_type="CATEGORY", target_key=str(cat.id),
            target_name="招牌面", score=0.9, raw_weight=9.0, event_count=4,
        ),
        CustomerTasteProfile(
            customer_id=c.id, dim_type="PRODUCT", target_key=str(p.id),
            target_name="麻辣小面", score=0.8, raw_weight=8.0, event_count=3,
        ),
        CustomerTasteProfile(
            customer_id=c.id, dim_type="TASTE_TAG", target_key="SPICY",
            target_name="辣", score=0.7, raw_weight=7.0, event_count=2,
        ),
    ])
    # 建规格偏好
    db.add(CustomerPreference(
        customer_id=c.id, category="SPICY", option_code="MILD",
        option_name="微辣", preference_score="0.6500", order_count=2,
    ))
    await db.flush()

    resp = await _call(get_my_taste_profile, db, c)
    data = resp["data"]
    assert data["customer_id"] == c.id
    dims = data["dimensions"]
    assert "CATEGORY" in dims and "PRODUCT" in dims and "TASTE_TAG" in dims
    assert len(dims["CATEGORY"]) == 1
    assert dims["CATEGORY"][0]["target_name"] == "招牌面"
    assert dims["PRODUCT"][0]["target_key"] == str(p.id)
    assert dims["TASTE_TAG"][0]["target_key"] == "SPICY"
    assert data["spec_preferences"], "spec_preferences 不应为空"
    assert data["spec_preferences"][0]["option_code"] == "MILD"


# ---------- TR-13.1 ③ 清除后三表为空 + 订单仍在 + 审计有记录 ----------

@pytest.mark.asyncio
async def test_clear_taste_profile_keeps_orders_and_logs_audit(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name="红烧牛肉面")
    c = await make_customer(db)

    # 建订单
    order = await make_paid_order(db, c.id, store.id, [(p, 2, [("辣度", "微辣", "MILD")])])

    # 建行为事件
    await ingest_events(db, c, [
        _ev("c1", "ORDER_PAID", product_id=p.id, store_id=store.id),
        _ev("c2", "CLICK", product_id=p.id, store_id=store.id, scene="menu"),
    ])
    # 建画像
    db.add(CustomerTasteProfile(
        customer_id=c.id, dim_type="CATEGORY", target_key=str(cat.id),
        target_name="招牌面", score=0.9, raw_weight=9.0, event_count=2,
    ))
    # 建评价
    db.add(ProductFeedback(
        order_id=order.id, product_id=p.id, customer_id=c.id, store_id=store.id,
        verdicts=["TASTY"], sentiment="POSITIVE",
    ))
    # 建规格偏好
    db.add(CustomerPreference(
        customer_id=c.id, category="SPICY", option_code="MILD",
        option_name="微辣", preference_score="0.6500", order_count=2,
    ))
    await db.flush()

    # 清除画像
    resp = await _call(clear_my_taste_profile, db, c)
    await db.commit()
    assert resp["data"]["cleared"] is True
    assert resp["data"]["events_deleted"] == 2

    # 三表为空
    ev = (await db.execute(select(func.count()).select_from(CustomerBehaviorEvent)
        .where(CustomerBehaviorEvent.customer_id == c.id))).scalar_one()
    tp = (await db.execute(select(func.count()).select_from(CustomerTasteProfile)
        .where(CustomerTasteProfile.customer_id == c.id))).scalar_one()
    pf = (await db.execute(select(func.count()).select_from(ProductFeedback)
        .where(ProductFeedback.customer_id == c.id))).scalar_one()
    cp = (await db.execute(select(func.count()).select_from(CustomerPreference)
        .where(CustomerPreference.customer_id == c.id))).scalar_one()
    assert ev == 0 and tp == 0 and pf == 0 and cp == 0

    # 订单本体保留
    order_count = (await db.execute(select(func.count()).select_from(Order)
        .where(Order.id == order.id))).scalar_one()
    assert order_count == 1

    # 审计有记录
    audit = (await db.execute(select(OperationLog).where(
        OperationLog.action == "privacy.clear_taste_profile",
        OperationLog.target_id == c.id,
    ))).scalars().all()
    assert len(audit) == 1
    assert audit[0].module == "privacy"
    assert audit[0].category == "CUSTOMER"


# ---------- TR-13.1 ④ 超期事件被任务清理 ----------

@pytest.mark.asyncio
async def test_cleanup_expired_events_drops_old_rows(db_session):
    db = db_session
    store = await make_store(db)
    p = await make_product(db, store.id, (await make_category(db, store.id)).id, name="小面")
    c = await make_customer(db)

    # 建两条事件：一条 91 天前（超期），一条 10 天前（保留）
    old_ev = CustomerBehaviorEvent(
        event_id=f"old-{uuid.uuid4().hex[:12]}",
        customer_id=c.id, event_type="CLICK", product_id=p.id, store_id=store.id,
        created_at=utcnow() - timedelta(days=91),
    )
    new_ev = CustomerBehaviorEvent(
        event_id=f"new-{uuid.uuid4().hex[:12]}",
        customer_id=c.id, event_type="CLICK", product_id=p.id, store_id=store.id,
        created_at=utcnow() - timedelta(days=10),
    )
    db.add_all([old_ev, new_ev])
    await db.flush()

    # cleanup 用 90 天留存
    n = await PrivacyService.cleanup_expired_events(db, retention_days=90)
    assert n == 1
    remaining = (await db.execute(select(CustomerBehaviorEvent.event_id)
        .where(CustomerBehaviorEvent.customer_id == c.id))).scalars().all()
    assert set(remaining) == {new_ev.event_id}


# ---------- TR-13.1 ⑤ consent 版本/时间/opt_in 落库 ----------

@pytest.mark.asyncio
async def test_consent_records_version_timestamp_and_opt_in(db_session):
    db = db_session
    c = await make_customer(db, recommend_opt_in=0)
    assert c.consent_version is None
    assert c.recommend_consent_at is None

    resp = await _call(
        record_privacy_consent, db, c, payload=PrivacyConsentRequest(version="1.0")
    )
    await db.commit()
    data = resp["data"]
    assert data["consent_version"] == "1.0"
    assert data["consented_at"] is not None
    assert data["recommend_opt_in"] == 1

    await db.refresh(c)
    assert c.consent_version == "1.0"
    assert c.recommend_consent_at is not None
    assert int(c.recommend_opt_in) == 1

    # 审计记录
    audit = (await db.execute(select(OperationLog).where(
        OperationLog.action == "privacy.consent",
        OperationLog.target_id == c.id,
    ))).scalars().all()
    assert len(audit) == 1


# ---------- TR-13.1 ⑥ A 令牌不能操作 B（鉴权天然隔离） ----------

@pytest.mark.asyncio
async def test_customer_a_cannot_operate_customer_b_via_token(db_session):
    """CurrentCustomer 从令牌解析，路由不接收 customer_id 参数，A 的令牌只会得到 A 自己。
    测试中模拟 A 持令牌调隐私接口，PrivacyService 只会作用于传入的 customer=A，
    B 的画像/事件不受影响。"""
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id, name="麻辣小面")
    a = await make_customer(db)
    b = await make_customer(db)

    # B 有画像与事件
    db.add(CustomerTasteProfile(
        customer_id=b.id, dim_type="CATEGORY", target_key=str(cat.id),
        target_name="招牌面", score=0.9, raw_weight=9.0, event_count=4,
    ))
    await ingest_events(db, b, [_ev("b-1", "CLICK", product_id=p.id, store_id=store.id)])

    # A 调自己的隐私接口（不传 B）—— A 看不到 B 的画像
    resp = await _call(get_my_taste_profile, db, a)
    data = resp["data"]
    assert data["customer_id"] == a.id
    # A 自己没有任何画像
    for dim_rows in data["dimensions"].values():
        assert dim_rows == []

    # A 清除自己的画像 —— 不会动 B 的数据
    await _call(clear_my_taste_profile, db, a)
    await db.commit()

    b_tp = (await db.execute(select(func.count()).select_from(CustomerTasteProfile)
        .where(CustomerTasteProfile.customer_id == b.id))).scalar_one()
    b_ev = (await db.execute(select(func.count()).select_from(CustomerBehaviorEvent)
        .where(CustomerBehaviorEvent.customer_id == b.id))).scalar_one()
    assert b_tp == 1, "A 清除不能影响 B 的画像"
    assert b_ev == 1, "A 清除不能影响 B 的事件"

    # A 的隐私设置中也不会泄漏 B 的 opt_in
    a_settings = await _call(get_my_privacy_settings, db, a)
    assert a_settings["data"]["recommend_opt_in"] == 1
