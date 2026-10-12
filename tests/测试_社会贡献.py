"""社会贡献 P0：限次、互斥、台账、门店隔离、幂等。"""
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.response import money
from app.models import (
    Customer,
    MemberLevel,
    Product,
    ProductCategory,
    SocialBenefitRedemption,
    SocialBenefitRule,
    SocialIdentityType,
    Store,
    SystemSetting,
)
from app.services.social_benefit_service import SocialBenefitService


async def _seed_base(db):
    store1 = Store(store_no="T01", name="测店1", status=1)
    store2 = Store(store_no="T02", name="测店2", status=1)
    db.add_all([store1, store2])
    await db.flush()
    lv = MemberLevel(name="普通会员", min_points=0, discount_rate=Decimal("0.95"))
    db.add(lv)
    await db.flush()
    c1 = Customer(
        customer_no="C-SB-1",
        display_name="环卫客",
        member_status="MEMBER",
        member_level_id=lv.id,
        status=1,
        registered_store_id=store1.id,
    )
    c2 = Customer(
        customer_no="C-SB-2",
        display_name="无身份客",
        member_status="MEMBER",
        member_level_id=lv.id,
        status=1,
        registered_store_id=store1.id,
    )
    db.add_all([c1, c2])
    await db.flush()
    db.add(
        SocialIdentityType(
            code="SANITATION", name="环卫工", sort_order=10, status=1, default_verify_hints=["工牌"]
        )
    )
    await db.flush()
    rule = SocialBenefitRule(
        store_id=None,
        identity_code="SANITATION",
        name="环卫工全天8折",
        benefit_type="ORDER_DISCOUNT",
        discount_rate=Decimal("0.80"),
        daily_limit=1,
        stack_policy="EXCLUSIVE",
        max_subsidy_amount=Decimal("20.00"),
        priority=10,
        status=1,
        time_windows=[["00:00", "23:59"]],
    )
    db.add(rule)
    await db.flush()
    cat = ProductCategory(store_id=store1.id, name="面类", status=1, sort_order=1)
    db.add(cat)
    await db.flush()
    prod = Product(
        store_id=store1.id,
        category_id=cat.id,
        product_no="P-SB-1",
        name="肠旺面",
        price=Decimal("20.00"),
        status="ON_SALE",
        no_member_discount=False,
    )
    db.add(prod)
    await db.flush()
    db.add(SystemSetting(setting_key="enable_social_benefit", setting_value="true"))
    await db.flush()
    from app.services.system_settings_service import SystemSettingsService

    SystemSettingsService.invalidate_cache_local()
    await SystemSettingsService._load_raw(db)
    await db.commit()
    return {
        "store1": store1,
        "store2": store2,
        "c1": c1,
        "c2": c2,
        "rule": rule,
        "prod": prod,
        "lv": lv,
    }


@pytest_asyncio.fixture
async def sb(db_session):
    return await _seed_base(db_session)


@pytest.mark.asyncio
async def test_无身份无折(db_session, sb):
    hit = await SocialBenefitService.find_best_rule(
        db_session, customer_id=sb["c2"].id, store_id=sb["store1"].id
    )
    assert hit is None
    prev = await SocialBenefitService.preview(
        db_session,
        customer_id=sb["c2"].id,
        store_id=sb["store1"].id,
        subtotal=money("20"),
        discountable_subtotal=money("20"),
    )
    assert prev["eligible"] is False


@pytest.mark.asyncio
async def test_环卫8折命中(db_session, sb):
    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.commit()
    prev = await SocialBenefitService.preview(
        db_session,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        subtotal=money("20"),
        discountable_subtotal=money("20"),
    )
    assert prev["eligible"] is True
    assert prev["discount_rate"] == "0.80"
    assert prev["subsidy_amount"] == "4.00"
    assert prev["total_amount"] == "16.00"


@pytest.mark.asyncio
async def test_同店同日限次(db_session, sb):
    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.flush()
    rule = sb["rule"]
    await SocialBenefitService.reserve_for_order(
        db_session,
        order_id=9001,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        rule=rule,
        identity_code="SANITATION",
        subsidy_amount=money("4"),
    )
    await db_session.commit()
    hit = await SocialBenefitService.find_best_rule(
        db_session, customer_id=sb["c1"].id, store_id=sb["store1"].id
    )
    assert hit is None


@pytest.mark.asyncio
async def test_门店隔离限次(db_session, sb):
    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.flush()
    await SocialBenefitService.reserve_for_order(
        db_session,
        order_id=9002,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        rule=sb["rule"],
        identity_code="SANITATION",
        subsidy_amount=money("4"),
    )
    await db_session.commit()
    # 另一店仍可享
    hit = await SocialBenefitService.find_best_rule(
        db_session, customer_id=sb["c1"].id, store_id=sb["store2"].id
    )
    assert hit is not None


@pytest.mark.asyncio
async def test_幂等台账(db_session, sb):
    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.flush()
    r1 = await SocialBenefitService.reserve_for_order(
        db_session,
        order_id=9003,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        rule=sb["rule"],
        identity_code="SANITATION",
        subsidy_amount=money("4"),
    )
    r2 = await SocialBenefitService.reserve_for_order(
        db_session,
        order_id=9003,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        rule=sb["rule"],
        identity_code="SANITATION",
        subsidy_amount=money("4"),
    )
    assert r1.id == r2.id
    rows = (
        await db_session.execute(
            select(SocialBenefitRedemption).where(SocialBenefitRedemption.order_id == 9003)
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_支付确认与退款冲减(db_session, sb):
    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.flush()
    row = await SocialBenefitService.reserve_for_order(
        db_session,
        order_id=9004,
        customer_id=sb["c1"].id,
        store_id=sb["store1"].id,
        rule=sb["rule"],
        identity_code="SANITATION",
        subsidy_amount=money("4"),
    )
    assert row.status == "PENDING"
    await SocialBenefitService.confirm_paid(db_session, 9004)
    await db_session.refresh(row)
    assert row.status == "NORMAL"
    await SocialBenefitService.reverse_for_order(db_session, 9004)
    await db_session.refresh(row)
    assert row.status == "REVERSED"
