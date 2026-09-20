"""Task 11：后台推荐效果报表 —— 24h 同顾客同品有序漏斗 + 归因 GMV。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import CustomerBehaviorEvent
from app.services.ab_service import AbService
from tests.reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
    utcnow,
)


def _ev(eid, cid, pid, etype, *, scene="recommend", bucket=None, when=None, payload=None):
    return CustomerBehaviorEvent(
        event_id=eid,
        customer_id=cid,
        store_id=None,
        event_type=etype,
        scene=scene,
        product_id=pid,
        ab_bucket=bucket,
        payload=payload,
        created_at=when or utcnow(),
    )


@pytest.mark.asyncio
async def test_funnel_matches_ordered_steps_within_24h(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p1 = await make_product(db, store.id, cat.id, name="红烧牛肉面", price="28.00")
    p2 = await make_product(db, store.id, cat.id, name="青菜", price="12.00")
    p3 = await make_product(db, store.id, cat.id, name="双份套餐", price="35.00")
    p4 = await make_product(db, store.id, cat.id, name="超时未支付品", price="40.00")
    c1 = await make_customer(db)
    c2 = await make_customer(db)
    c3 = await make_customer(db)
    c4 = await make_customer(db)
    await AbService.ensure_default_experiment(db)
    await db.commit()

    t = utcnow() - timedelta(minutes=10)

    # v1：c1/p1 完整链路并支付；c2/p2 仅曝光
    o1 = await make_paid_order(db, c1.id, store.id, [(p1, 1, [])])
    db.add_all([
        _ev("v1-e1", c1.id, p1.id, "EXPOSE", bucket="v1", when=t),
        _ev("v1-c1", c1.id, p1.id, "CLICK", bucket="v1", when=t + timedelta(hours=1)),
        _ev("v1-a1", c1.id, p1.id, "CART_ADD", bucket="v1", when=t + timedelta(hours=2),
            payload={"qty": 1}),
        _ev("v1-p1", c1.id, p1.id, "ORDER_PAID", scene="order", bucket=None,
            when=t + timedelta(hours=3), payload={"order_id": o1.id, "qty": 1}),
        _ev("v1-e2", c2.id, p2.id, "EXPOSE", bucket="v1", when=t),
        # 干扰：菜单自然点击（无桶、scene=menu）不得计入漏斗
        _ev("noise1", c2.id, p2.id, "CLICK", scene="menu", bucket=None, when=t),
    ])

    # v2：c3/p3 买 2 份；c4/p4 支付晚于加购 25h，不转化
    o3 = await make_paid_order(db, c3.id, store.id, [(p3, 2, [])])
    db.add_all([
        _ev("v2-e3", c3.id, p3.id, "EXPOSE", bucket="v2", when=t),
        _ev("v2-c3", c3.id, p3.id, "CLICK", bucket="v2", when=t + timedelta(minutes=30)),
        _ev("v2-a3", c3.id, p3.id, "CART_ADD", bucket="v2", when=t + timedelta(hours=1),
            payload={"qty": 2}),
        _ev("v2-p3", c3.id, p3.id, "ORDER_PAID", scene="order", bucket=None,
            when=t + timedelta(hours=2), payload={"order_id": o3.id, "qty": 2}),
        _ev("v2-e4", c4.id, p4.id, "EXPOSE", bucket="v2", when=t),
        _ev("v2-c4", c4.id, p4.id, "CLICK", bucket="v2", when=t + timedelta(hours=1)),
        _ev("v2-a4", c4.id, p4.id, "CART_ADD", bucket="v2", when=t + timedelta(hours=2)),
        _ev("v2-p4", c4.id, p4.id, "ORDER_PAID", scene="order", bucket=None,
            when=t + timedelta(hours=25)),
    ])
    await db.flush()

    report = await AbService.experiment_report(db, "reco_algo")
    by_code = {b["code"]: b for b in report["buckets"]}
    assert report["funnel_window_hours"] == 24
    assert report["scene_scope"] == ["recommend", "settle_addon"]
    assert "24" in report["attribution_note"]

    f1 = by_code["v1"]["funnel"]
    assert f1["exposed_pairs"] == 2
    assert f1["click_pairs"] == 1
    assert f1["cart_pairs"] == 1
    assert f1["order_pairs"] == 1
    assert f1["click_rate"] == 0.5
    assert f1["adopt_rate"] == 0.5
    assert f1["order_conv"] == 0.5
    assert f1["attributed_paid_users"] == 1
    assert f1["attributed_gmv"] == "28.00"
    assert f1["gmv_per_paid_user"] == "28.00"

    f2 = by_code["v2"]["funnel"]
    assert f2["exposed_pairs"] == 2
    assert f2["click_pairs"] == 2
    assert f2["cart_pairs"] == 2
    assert f2["order_pairs"] == 1
    assert f2["attributed_gmv"] == "70.00"
    assert f2["gmv_per_paid_user"] == "70.00"
    # 旧口径字段保留
    assert by_code["v1"]["ctr"] is not None


@pytest.mark.asyncio
async def test_funnel_step_outside_24h_breaks_chain(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id, price="20.00")
    c = await make_customer(db)
    await AbService.ensure_default_experiment(db)
    await db.commit()

    t = utcnow() - timedelta(hours=2)
    # 点击在曝光后 25 小时：链路在 CLICK 即断裂
    db.add_all([
        _ev("b-e", c.id, p.id, "EXPOSE", bucket="v1", when=t),
        _ev("b-c", c.id, p.id, "CLICK", bucket="v1", when=t + timedelta(hours=25)),
        _ev("b-a", c.id, p.id, "CART_ADD", bucket="v1", when=t + timedelta(hours=26)),
    ])
    await db.flush()

    report = await AbService.experiment_report(db, "reco_algo")
    f = {b["code"]: b for b in report["buckets"]}["v1"]["funnel"]
    assert f["exposed_pairs"] == 1
    assert f["click_pairs"] == 0
    assert f["cart_pairs"] == 0
    assert f["order_pairs"] == 0
    assert f["attributed_gmv"] == "0.00"
    assert f["gmv_per_paid_user"] is None


@pytest.mark.asyncio
async def test_funnel_respects_date_window(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id, price="26.00")
    c = await make_customer(db)
    await AbService.ensure_default_experiment(db)
    await db.commit()

    # 完整链路发生在 3 天前
    t = utcnow() - timedelta(days=3)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])], days_ago=3)
    db.add_all([
        _ev("old-e", c.id, p.id, "EXPOSE", bucket="v1", when=t),
        _ev("old-c", c.id, p.id, "CLICK", bucket="v1", when=t + timedelta(hours=1)),
        _ev("old-a", c.id, p.id, "CART_ADD", bucket="v1", when=t + timedelta(hours=2)),
        _ev("old-p", c.id, p.id, "ORDER_PAID", scene="order", when=t + timedelta(hours=3),
            payload={"order_id": order.id, "qty": 1}),
    ])
    await db.flush()

    date_from = utcnow() - timedelta(days=1)
    report = await AbService.experiment_report(db, "reco_algo", date_from, None)
    f = {b["code"]: b for b in report["buckets"]}["v1"]["funnel"]
    assert f["exposed_pairs"] == 0
    assert f["attributed_gmv"] == "0.00"


@pytest.mark.asyncio
async def test_settle_addon_scene_included(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id, price="18.00")
    c = await make_customer(db)
    await AbService.ensure_default_experiment(db)
    await db.commit()

    t = utcnow() - timedelta(minutes=5)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])
    db.add_all([
        _ev("s-e", c.id, p.id, "EXPOSE", scene="settle_addon", bucket="v2", when=t),
        _ev("s-c", c.id, p.id, "CLICK", scene="settle_addon", bucket="v2", when=t),
        _ev("s-a", c.id, p.id, "CART_ADD", scene="settle_addon", bucket="v2",
            when=t + timedelta(minutes=10)),
        _ev("s-p", c.id, p.id, "ORDER_PAID", scene="order", when=t + timedelta(minutes=20),
            payload={"order_id": order.id, "qty": 1}),
    ])
    await db.flush()

    report = await AbService.experiment_report(db, "reco_algo")
    f = {b["code"]: b for b in report["buckets"]}["v2"]["funnel"]
    assert f["exposed_pairs"] == 1
    assert f["order_pairs"] == 1
    assert f["attributed_gmv"] == "18.00"
