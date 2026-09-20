"""Task 1 验收：智能推荐新模型建表、默认值、唯一约束与迁移链。"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    AbAssignment,
    AbExperiment,
    Customer,
    CustomerBehaviorEvent,
    CustomerTasteProfile,
    ProductFeedback,
    ProductTasteProfile,
)
from reco_factories import make_category, make_customer, make_product, make_store


@pytest.mark.asyncio
async def test_new_tables_crud_smoke(db_session):
    db = db_session
    store = await make_store(db)
    cat = await make_category(db, store.id)
    p = await make_product(db, store.id, cat.id)
    c = await make_customer(db)

    db.add(ProductTasteProfile(
        product_id=p.id, store_id=store.id,
        dimensions={"spicy": 2}, tags=["微辣"], source="AUTO", manual_overrides=[],
    ))
    db.add(CustomerTasteProfile(
        customer_id=c.id, dim_type="CATEGORY", target_key=str(cat.id),
        target_name=cat.name, event_count=1,
    ))
    db.add(CustomerBehaviorEvent(
        customer_id=c.id, store_id=store.id, event_id="evt-1",
        event_type="CLICK", scene="recommend", product_id=p.id, category_id=cat.id,
    ))
    db.add(ProductFeedback(
        order_id=1, product_id=p.id, customer_id=c.id, store_id=store.id,
        verdicts=["TASTY"], sentiment="POSITIVE",
    ))
    db.add(AbExperiment(
        experiment_key="reco_algo", name="推荐算法实验", status="DRAFT",
        traffic_percent=50, buckets=[{"code": "v1", "traffic": 50}, {"code": "v2", "traffic": 50}],
    ))
    db.add(AbAssignment(
        experiment_key="reco_algo", customer_id=c.id, bucket="v2",
    ))
    await db.flush()

    assert await db.get(ProductTasteProfile, 1)
    assert (await db.get(Customer, c.id)).recommend_opt_in == 1


@pytest.mark.asyncio
async def test_unique_event_id(db_session):
    db = db_session
    c = await make_customer(db)
    for i in range(2):
        db.add(CustomerBehaviorEvent(
            customer_id=c.id, event_id="dup-evt", event_type="CLICK",
        ))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_unique_taste_profile_dim(db_session):
    db = db_session
    c = await make_customer(db)
    for i in range(2):
        db.add(CustomerTasteProfile(
            customer_id=c.id, dim_type="TASTE_TAG", target_key="辣",
        ))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_unique_feedback(db_session):
    db = db_session
    store = await make_store(db, store_no="S-FB")
    cat = await make_category(db, store.id, category_no="C-FB")
    p = await make_product(db, store.id, cat.id, product_no="P-FB")
    c = await make_customer(db)
    for i in range(2):
        db.add(ProductFeedback(
            order_id=99, product_id=p.id, customer_id=c.id,
            verdicts=["DISLIKE"], sentiment="NEGATIVE",
        ))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_unique_ab_assignment(db_session):
    db = db_session
    c = await make_customer(db)
    for i in range(2):
        db.add(AbAssignment(experiment_key="reco_algo", customer_id=c.id, bucket="v1"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_customer_privacy_defaults(db_session):
    db = db_session
    c = await make_customer(db)
    await db.flush()
    got = await db.get(Customer, c.id)
    assert got.recommend_opt_in == 1
    assert got.consent_version is None
    assert got.recommend_consent_at is None


def test_migration_chain():
    """0018 迁移 revision 链接到 0017，且文件可被 alembic 发现。"""
    mig_path = (
        Path(__file__).resolve().parents[1]
        / "backend" / "alembic" / "versions" / "0018_taste_recommendation.py"
    )
    spec = importlib.util.spec_from_file_location("m0018", mig_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0018_taste_recommendation"
    assert mod.down_revision == "0017_fin_ledger_idempotency"
