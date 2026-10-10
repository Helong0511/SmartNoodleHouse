"""Task 6 验收：轻量评价白名单/sentiment 推导、本人已付校验、幂等、画像正/负向更新、opt_out。"""
import pytest
from sqlalchemy import select

from app.api.miniprogram import _load_owned_order, submit_order_feedback
from app.core.exceptions import AppException
from app.models import CustomerTasteProfile, ProductFeedback
from app.schemas.reco import FeedbackBatchIn, FeedbackItemIn
from app.services.feedback_service import FeedbackService, derive_sentiment
from reco_factories import (
    make_category,
    make_customer,
    make_paid_order,
    make_product,
    make_store,
)


async def _profile_rows(db, customer_id):
    return {
        (r.dim_type, r.target_key): r
        for r in (await db.execute(
            select(CustomerTasteProfile).where(CustomerTasteProfile.customer_id == customer_id)
        )).scalars().all()
    }


def test_derive_sentiment_from_verdicts():
    assert derive_sentiment(["TASTY", "WILL_REORDER"]) == "POSITIVE"
    assert derive_sentiment(["DISLIKE", "TOO_SPICY"]) == "NEGATIVE"
    assert derive_sentiment(["PORTION_OK"]) == "NEUTRAL"
    # 正负抵消 → NEUTRAL
    assert derive_sentiment(["TASTY", "TOO_SALTY"]) == "NEUTRAL"
    # 负多于正
    assert derive_sentiment(["TASTY", "TOO_SWEET", "DISLIKE"]) == "NEGATIVE"


@pytest.mark.asyncio
async def test_submit_positive_feedback_updates_profile(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="招牌面")
    p = await make_product(db, store.id, cat.id, name="麻辣小面")
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    result = await FeedbackService.submit_order_feedback(db, order, [
        {"product_id": p.id, "verdicts": ["TASTY", "WILL_REORDER"]},
    ])
    assert result["saved"] == [p.id]
    assert result["skipped_existing"] == []
    fb = (await db.execute(select(ProductFeedback))).scalar_one()
    assert fb.sentiment == "POSITIVE"
    assert set(fb.verdicts) == {"TASTY", "WILL_REORDER"}

    rows = await _profile_rows(db, c.id)
    assert float(rows[("PRODUCT", str(p.id))].raw_weight) == 2.0
    assert float(rows[("PRODUCT", str(p.id))].score) > 0.5


@pytest.mark.asyncio
async def test_submit_negative_feedback_lowers_profile(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id, name="饮品")
    p = await make_product(db, store.id, cat.id, name="冰镇柠檬茶")
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    await FeedbackService.submit_order_feedback(db, order, [
        {"product_id": p.id, "verdicts": ["TOO_SWEET", "DISLIKE"]},
    ])
    rows = await _profile_rows(db, c.id)
    # 两条负标签 → -3
    assert float(rows[("PRODUCT", str(p.id))].raw_weight) == -3.0
    assert float(rows[("PRODUCT", str(p.id))].score) < 0.5


@pytest.mark.asyncio
async def test_duplicate_submit_is_idempotent(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id)
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    r1 = await FeedbackService.submit_order_feedback(
        db, order, [{"product_id": p.id, "verdicts": ["TASTY"]}]
    )
    r2 = await FeedbackService.submit_order_feedback(
        db, order, [{"product_id": p.id, "verdicts": ["DISLIKE"]}]
    )
    assert r1["saved"] == [p.id]
    assert r2["saved"] == []
    assert r2["skipped_existing"] == [p.id]
    # 仅一条评价记录，画像只 fold 一次正向
    total = len(list((await db.execute(select(ProductFeedback))).scalars().all()))
    assert total == 1
    rows = await _profile_rows(db, c.id)
    assert float(rows[("PRODUCT", str(p.id))].raw_weight) == 2.0


@pytest.mark.asyncio
async def test_product_not_in_order_and_illegal_verdict_rejected(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p1 = await make_product(db, store.id, cat.id)
    p2 = await make_product(db, store.id, cat.id)
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p1, 1, [])])

    with pytest.raises(ValueError, match="不属于"):
        await FeedbackService.submit_order_feedback(
            db, order, [{"product_id": p2.id, "verdicts": ["TASTY"]}]
        )
    with pytest.raises(ValueError, match="非法"):
        await FeedbackService.submit_order_feedback(
            db, order, [{"product_id": p1.id, "verdicts": ["HACK_TAG"]}]
        )


@pytest.mark.asyncio
async def test_other_customer_order_403(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id)
    owner = await make_customer(db)
    other = await make_customer(db)
    order = await make_paid_order(db, owner.id, store.id, [(p, 1, [])])

    with pytest.raises(AppException) as exc:
        await _load_owned_order(db, order.id, other)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unpaid_order_rejected_by_route(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id)
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])], payment_status="PENDING")

    payload = FeedbackBatchIn(items=[FeedbackItemIn(product_id=p.id, verdicts=["TASTY"])])
    with pytest.raises(AppException) as exc:
        # request 在校验失败前不会被使用
        await submit_order_feedback(order.id, payload, request=None, db=db, customer=c)
    assert exc.value.code == 40061


@pytest.mark.asyncio
async def test_feedback_payload_echo(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id, name="酸辣粉")
    c = await make_customer(db)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    payload = await FeedbackService.order_feedback_payload(db, order)
    assert payload["can_evaluate"] is True
    assert len(payload["items"]) == 1
    assert payload["items"][0]["feedback"] is None

    await FeedbackService.submit_order_feedback(
        db, order, [{"product_id": p.id, "verdicts": ["FRESH"], "remark": "不错"}]
    )
    payload2 = await FeedbackService.order_feedback_payload(db, order)
    fb = payload2["items"][0]["feedback"]
    assert fb["verdicts"] == ["FRESH"]
    assert fb["sentiment"] == "POSITIVE"
    assert fb["remark"] == "不错"


@pytest.mark.asyncio
async def test_opt_out_feedback_stored_but_profile_untouched(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id)
    c = await make_customer(db, recommend_opt_in=0)
    order = await make_paid_order(db, c.id, store.id, [(p, 1, [])])

    result = await FeedbackService.submit_order_feedback(
        db, order, [{"product_id": p.id, "verdicts": ["DISLIKE"]}]
    )
    assert result["saved"] == [p.id]
    # 评价留存，但画像不学习
    assert len(list((await db.execute(select(ProductFeedback))).scalars().all())) == 1
    assert len(list((await db.execute(select(CustomerTasteProfile))).scalars().all())) == 0
