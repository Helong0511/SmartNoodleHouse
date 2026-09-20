"""Task 5 验收：事件 fold 权重、三维画像、水位幂等、衰减、支付/退款/评价挂接、opt_out 跳过。"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import CustomerBehaviorEvent, CustomerTasteProfile, ProductFeedback
from app.services.taste_learning_service import (
    TasteLearningService,
    _decay_factor,
    _logistic,
)
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
    utcnow,
)


async def _setup_product(db, name="麻辣小面"):
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name=name)
    return store, cat, p


async def _rows(db, customer_id: int) -> dict[tuple[str, str], CustomerTasteProfile]:
    result = await db.execute(
        select(CustomerTasteProfile).where(CustomerTasteProfile.customer_id == customer_id)
    )
    return {(r.dim_type, r.target_key): r for r in result.scalars().all()}


def _raw_event(customer_id, etype, pid, *, when=None, qty=None, store_id=None, scene=None):
    payload = {"qty": qty} if qty else None
    return CustomerBehaviorEvent(
        event_id=f"{etype}-{pid}-{uuid.uuid4().hex[:12]}",
        customer_id=customer_id,
        store_id=store_id,
        event_type=etype,
        scene=scene,
        product_id=pid,
        payload=payload,
        created_at=when or utcnow(),
    )


@pytest.mark.asyncio
async def test_fold_weights_build_three_dimensions(db_session):
    db = db_session
    store, cat, p = await _setup_product(db)
    c = await make_customer(db)
    # CLICK 1 + CART_ADD 2 + ORDER_PAID(qty2) 6 = raw 9
    db.add_all([
        _raw_event(c.id, "CLICK", p.id, store_id=store.id, scene="menu"),
        _raw_event(c.id, "CART_ADD", p.id, store_id=store.id, qty=1),
        _raw_event(c.id, "ORDER_PAID", p.id, store_id=store.id, qty=2),
    ])
    await db.flush()

    n = await TasteLearningService.learn_from_events(db, c.id)
    assert n == 3
    rows = await _rows(db, c.id)
    product_row = rows[("PRODUCT", str(p.id))]
    assert product_row.event_count == 3
    assert float(product_row.raw_weight) == 9.0
    assert float(product_row.score) == round(_logistic(9.0), 4)
    assert float(product_row.score) > 0.85
    # 分类画像
    cat_row = rows[("CATEGORY", str(cat.id))]
    assert float(cat_row.raw_weight) == 9.0
    assert cat_row.target_name == "招牌面"
    # 标签/维度画像：麻辣小面 → 标签「麻辣」+ dim:spicy=2 + dim:numbing=2
    assert ("TASTE_TAG", "tag:麻辣") in rows
    assert ("TASTE_TAG", "dim:spicy=2") in rows
    assert ("TASTE_TAG", "dim:numbing=2") in rows


@pytest.mark.asyncio
async def test_watermark_makes_second_learn_noop(db_session):
    db = db_session
    _, _, p = await _setup_product(db)
    c = await make_customer(db)
    db.add(_raw_event(c.id, "CLICK", p.id))
    await db.flush()
    await TasteLearningService.learn_from_events(db, c.id)
    rows1 = await _rows(db, c.id)
    score_before = float(rows1[("PRODUCT", str(p.id))].score)

    # 无新事件 → 处理 0 条；即使再扫一遍也不重复累计
    n = await TasteLearningService.learn_from_events(db, c.id)
    assert n == 0
    rows2 = await _rows(db, c.id)
    assert float(rows2[("PRODUCT", str(p.id))].score) == score_before
    assert rows2[("PRODUCT", str(p.id))].event_count == 1


@pytest.mark.asyncio
async def test_negative_events_push_score_below_half(db_session):
    db = db_session
    _, _, p = await _setup_product(db, name="酸辣粉")
    c = await make_customer(db)
    db.add_all([
        _raw_event(c.id, "CART_REMOVE", p.id, when=utcnow() - timedelta(minutes=5)),
        _raw_event(c.id, "CART_REMOVE", p.id),
    ])
    await db.flush()
    await TasteLearningService.learn_from_events(db, c.id)
    rows = await _rows(db, c.id)
    # -1.5 * 2 = -3（5 分钟前那条有微小时间衰减）→ logistic(-0.75) ≈ 0.32
    assert float(rows[("PRODUCT", str(p.id))].raw_weight) == pytest.approx(-3.0, abs=0.01)
    assert float(rows[("PRODUCT", str(p.id))].score) < 0.5


@pytest.mark.asyncio
async def test_half_life_decay_on_old_events(db_session):
    db = db_session
    _, _, p = await _setup_product(db)
    c = await make_customer(db)
    # 60 天前一次购买 raw 3，今天再买一次 raw 3
    db.add_all([
        _raw_event(c.id, "ORDER_PAID", p.id, when=utcnow() - timedelta(days=60), qty=1),
        _raw_event(c.id, "ORDER_PAID", p.id, when=utcnow(), qty=1),
    ])
    await db.flush()
    await TasteLearningService.learn_from_events(db, c.id)
    rows = await _rows(db, c.id)
    raw = float(rows[("PRODUCT", str(p.id))].raw_weight)
    # 3 * 2^(-2) + 3 = 3.75
    assert 3.7 < raw < 3.8

    # 纯函数：30 天恰好减半
    assert abs(_decay_factor(utcnow() - timedelta(days=30), utcnow()) - 0.5) < 0.01


@pytest.mark.asyncio
async def test_paid_order_events_and_idempotent_rerun(db_session):
    db = db_session
    store, cat, p1 = await _setup_product(db, name="麻辣小面")
    p2 = await make_product(db, store.id, cat.id, name="酸辣粉")
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p1, 1, []), (p2, 2, [])])

    n1 = await TasteLearningService.record_paid_order_events(db, order.id)
    assert n1 == 2
    rows = await _rows(db, c.id)
    assert float(rows[("PRODUCT", str(p1.id))].raw_weight) == 3.0
    assert float(rows[("PRODUCT", str(p2.id))].raw_weight) == 6.0
    events = list((await db.execute(select(CustomerBehaviorEvent))).scalars().all())
    assert {e.event_id for e in events} == {
        f"order_paid:{order.id}:{p1.id}",
        f"order_paid:{order.id}:{p2.id}",
    }

    # outbox 重试：无新事件、画像不翻倍
    n2 = await TasteLearningService.record_paid_order_events(db, order.id)
    assert n2 == 0
    rows2 = await _rows(db, c.id)
    assert float(rows2[("PRODUCT", str(p1.id))].raw_weight) == 3.0


@pytest.mark.asyncio
async def test_full_refund_records_negative_and_rerun_safe(db_session):
    db = db_session
    store, _, p = await _setup_product(db)
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])], payment_status="REFUNDED")

    n = await TasteLearningService.record_full_refund_events(db, order.id)
    assert n == 1
    rows = await _rows(db, c.id)
    assert float(rows[("PRODUCT", str(p.id))].raw_weight) == -1.5
    n2 = await TasteLearningService.record_full_refund_events(db, order.id)
    assert n2 == 0
    rows2 = await _rows(db, c.id)
    assert float(rows2[("PRODUCT", str(p.id))].raw_weight) == -1.5


@pytest.mark.asyncio
async def test_feedback_positive_and_negative(db_session):
    db = db_session
    store, _, p_good = await _setup_product(db, name="麻辣小面")
    cat2 = await make_category(db, store.id, name="饮品")
    p_bad = await make_product(db, store.id, cat2.id, name="冰镇柠檬茶")
    c = await make_customer(db)

    db.add(ProductFeedback(
        order_id=90001, product_id=p_good.id, customer_id=c.id, store_id=store.id,
        verdicts={"tags": ["好吃"]}, sentiment="POSITIVE",
    ))
    db.add(ProductFeedback(
        order_id=90002, product_id=p_bad.id, customer_id=c.id, store_id=store.id,
        verdicts={"tags": ["太甜"]}, sentiment="NEGATIVE",
    ))
    await db.flush()
    fb_rows = list((await db.execute(select(ProductFeedback))).scalars().all())
    for fb in fb_rows:
        await TasteLearningService.learn_from_feedback(db, fb)

    rows = await _rows(db, c.id)
    assert float(rows[("PRODUCT", str(p_good.id))].raw_weight) == 2.0
    assert float(rows[("PRODUCT", str(p_good.id))].score) > 0.5
    assert float(rows[("PRODUCT", str(p_bad.id))].raw_weight) == -3.0
    assert float(rows[("PRODUCT", str(p_bad.id))].score) < 0.5


@pytest.mark.asyncio
async def test_opt_out_paid_learning_skipped(db_session):
    db = db_session
    store, _, p = await _setup_product(db)
    c = await make_customer(db, recommend_opt_in=0)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    n = await TasteLearningService.record_paid_order_events(db, order.id)
    assert n == 0
    total_events = len(list((await db.execute(select(CustomerBehaviorEvent))).scalars().all()))
    total_profiles = len(list((await db.execute(select(CustomerTasteProfile))).scalars().all()))
    assert total_events == 0
    assert total_profiles == 0


@pytest.mark.asyncio
async def test_learn_recent_customers_scheduler_sweep(db_session):
    db = db_session
    _, _, p = await _setup_product(db)
    c = await make_customer(db)
    db.add(_raw_event(c.id, "CLICK", p.id))
    await db.flush()
    stats = await TasteLearningService.learn_recent_customers(db, since=utcnow() - timedelta(minutes=15))
    assert stats["customers"] == 1
    assert stats["events"] == 1
    rows = await _rows(db, c.id)
    assert ("PRODUCT", str(p.id)) in rows
