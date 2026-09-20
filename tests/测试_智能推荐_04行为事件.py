"""Task 4 验收：行为事件白名单、event_id 幂等、opt_out 丢弃、批量上限。"""
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import CustomerBehaviorEvent
from app.schemas.reco import BehaviorEventBatch, BehaviorEventIn
from app.services.behavior_event_service import ingest_events
from reco_factories import make_customer


def _ev(event_id, etype, **kw):
    return {"event_id": event_id, "event_type": etype, **kw}


@pytest.mark.asyncio
async def test_ingest_accepts_valid_and_drops_bad(db_session):
    db = db_session
    c = await make_customer(db)
    result = await ingest_events(db, c, [
        _ev("e1", "CLICK", product_id=1001, scene="menu", store_id=9, ab_bucket="reco_algo:v2"),
        _ev("e2", "CART_ADD", product_id=1002, payload={"qty": 2, "options": ["微辣"]}),
        _ev("e3", "HACK"),                       # 非法类型
        {"event_type": "EXPOSE", "product_id": 1003},  # 缺 event_id → 兜底生成
        "not-an-object",                          # 非对象
    ])
    assert result["accepted"] == 3
    assert result["duplicate"] == 0
    assert result["dropped"] == 2

    rows = list((await db.execute(select(CustomerBehaviorEvent))).scalars().all())
    assert len(rows) == 3
    by_type = {r.event_type: r for r in rows}
    assert by_type["CLICK"].scene == "menu"
    assert by_type["CLICK"].store_id == 9
    assert by_type["CLICK"].ab_bucket == "reco_algo:v2"
    assert by_type["CART_ADD"].payload == {"qty": 2, "options": ["微辣"]}
    # 缺 event_id 自动补 32 位 hex
    expose = by_type["EXPOSE"]
    assert len(expose.event_id) >= 16


@pytest.mark.asyncio
async def test_event_id_idempotent_across_and_within_batch(db_session):
    db = db_session
    c = await make_customer(db)
    r1 = await ingest_events(db, c, [_ev("dup-1", "CLICK", product_id=1)])
    assert r1["accepted"] == 1

    # 跨批次重复 → duplicate
    r2 = await ingest_events(db, c, [
        _ev("dup-1", "CLICK", product_id=1),
        _ev("dup-2", "CLICK", product_id=2),
    ])
    assert r2["accepted"] == 1
    assert r2["duplicate"] == 1

    # 同批次重复 → dropped（不查库，直接拒绝）
    r3 = await ingest_events(db, c, [
        _ev("dup-3", "CLICK", product_id=3),
        _ev("dup-3", "CLICK", product_id=3),
    ])
    assert r3["accepted"] == 1
    assert r3["dropped"] == 1

    total = (await db.execute(select(func.count()).select_from(CustomerBehaviorEvent))).scalar_one()
    assert total == 3


@pytest.mark.asyncio
async def test_opt_out_customer_events_dropped_and_not_stored(db_session):
    db = db_session
    c = await make_customer(db, recommend_opt_in=0)
    result = await ingest_events(db, c, [
        _ev("o1", "ORDER_PAID", product_id=1),
        _ev("o2", "CLICK", product_id=2),
    ])
    assert result == {"accepted": 0, "duplicate": 0, "dropped": 2, "reason": "opt_out"}
    total = (await db.execute(select(func.count()).select_from(CustomerBehaviorEvent))).scalar_one()
    assert total == 0


@pytest.mark.asyncio
async def test_field_truncation_and_payload_guard(db_session):
    db = db_session
    c = await make_customer(db)
    result = await ingest_events(db, c, [
        _ev("x1", "CLICK", scene="x" * 50, ab_bucket="b" * 200, payload=["not", "dict"]),
        _ev("x2", "CLICK", payload={"big": "y" * 5000}),
    ])
    assert result["accepted"] == 2
    rows = {r.event_id: r for r in (await db.execute(select(CustomerBehaviorEvent))).scalars().all()}
    assert len(rows["x1"].scene) == 20
    assert len(rows["x1"].ab_bucket) == 80
    assert rows["x1"].payload is None  # 非 dict 被剔除
    assert rows["x2"].payload is None  # 超 2KB 被剔除


def test_batch_schema_limit_50():
    BehaviorEventBatch(events=[BehaviorEventIn(event_id="a", event_type="CLICK")])
    with pytest.raises(ValidationError):
        BehaviorEventBatch(
            events=[BehaviorEventIn(event_id=f"e{i}", event_type="CLICK") for i in range(51)]
        )
