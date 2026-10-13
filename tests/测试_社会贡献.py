"""社会贡献 P0：限次、互斥、台账、门店隔离、幂等；小程序申请→审核。"""
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.exceptions import AppException
from app.core.response import money
from app.models import (
    Customer,
    CustomerSocialIdentity,
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


@pytest.mark.asyncio
async def test_小程序申请进入PENDING并可审核(db_session, sb):
    row = await SocialBenefitService.apply_identity(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        verify_method="MINI",
        credential_note="小程序申请",
    )
    await db_session.commit()
    assert row.status == SocialBenefitService.PENDING
    assert row.verify_method == "MINI"
    assert int(row.store_id) == int(sb["store1"].id)

    # 幂等：重复申请返回同一 PENDING
    again = await SocialBenefitService.apply_identity(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        verify_method="MINI",
        credential_note="再次点申请",
    )
    assert again.id == row.id

    items, total = await SocialBenefitService.list_identities(
        db_session, store_id=sb["store1"].id, status="PENDING"
    )
    assert total >= 1
    hit = next(x for x in items if int(x["id"]) == int(row.id))
    assert hit.get("customer_nickname") or hit.get("customer_no") or hit.get("customer_id")

    approved = await SocialBenefitService.review_identity(
        db_session,
        identity_id=int(row.id),
        approve=True,
        reviewer_id=1,
        store_id=sb["store1"].id,
    )
    await db_session.commit()
    assert approved.status == SocialBenefitService.ACTIVE

    # ACTIVE 后再申请应拒绝
    with pytest.raises(AppException) as ei:
        await SocialBenefitService.apply_identity(
            db_session,
            customer_id=sb["c1"].id,
            identity_code="SANITATION",
            store_id=sb["store1"].id,
            verify_method="MINI",
        )
    assert ei.value.code == 40901

    revoked = await SocialBenefitService.revoke_identity(
        db_session,
        identity_id=int(approved.id),
        operator_id=1,
        store_id=sb["store1"].id,
        reason="顾客管理取消",
    )
    await db_session.commit()
    assert revoked.status == SocialBenefitService.REVOKED
    assert "取消" in (revoked.reject_reason or "")

    # 取消后禁止再申请（任意公益身份）
    with pytest.raises(AppException) as ej:
        await SocialBenefitService.apply_identity(
            db_session,
            customer_id=sb["c1"].id,
            identity_code="SANITATION",
            store_id=sb["store1"].id,
            verify_method="MINI",
            credential_note="取消后再申请",
        )
    assert ej.value.code == 40901
    assert "不可再次申请" in ej.value.message


@pytest.mark.asyncio
async def test_拒绝后再申请可重新进入PENDING(db_session, sb):
    """拒绝后再次申请应复用记录并回到 PENDING；序列化不得 MissingGreenlet。"""
    row = await SocialBenefitService.apply_identity(
        db_session,
        customer_id=sb["c2"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        verify_method="MINI",
        credential_note="首次申请",
    )
    await db_session.commit()
    rejected = await SocialBenefitService.review_identity(
        db_session,
        identity_id=int(row.id),
        approve=False,
        reviewer_id=1,
        reject_reason="材料不全",
        store_id=sb["store1"].id,
    )
    await db_session.commit()
    assert rejected.status == SocialBenefitService.REJECTED

    again = await SocialBenefitService.apply_identity(
        db_session,
        customer_id=sb["c2"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        verify_method="WECHAT",
        credential_note="补材料再申请",
    )
    await db_session.commit()
    assert again.id == row.id
    assert again.status == SocialBenefitService.PENDING
    assert again.reject_reason is None
    assert again.credential_note == "补材料再申请"
    # 模拟接口：commit 后再读 created_at 也不应惰性加载失败
    payload = SocialBenefitService._identity_payload(again)
    assert payload["id"] == again.id
    assert payload["status"] == SocialBenefitService.PENDING
    assert payload.get("created_at")


@pytest.mark.asyncio
async def test_申请无效身份类型(db_session, sb):
    with pytest.raises(AppException) as ei:
        await SocialBenefitService.apply_identity(
            db_session,
            customer_id=sb["c1"].id,
            identity_code="NOT_A_TYPE",
            store_id=sb["store1"].id,
            verify_method="MINI",
        )
    assert ei.value.code == 40002
    assert "无效公益身份" in ei.value.message


@pytest.mark.asyncio
async def test_勾选公益无身份须报错(db_session, sb):
    """显式 use_social_benefit 但无 ACTIVE 身份时，禁止静默回落会员折。"""
    from app.services.customer_service import OrderCreateService

    with pytest.raises(AppException) as ei:
        await OrderCreateService.create_order(
            db_session,
            store_id=sb["store1"].id,
            table_token=None,
            customer_id=sb["c2"].id,  # 无公益身份
            channel="H5",
            dining_type="TAKEAWAY",
            need_pack=True,
            items=[{"product_id": sb["prod"].id, "quantity": 1, "option_ids": []}],
            use_social_benefit=True,
            idempotency_key="sb-no-identity-1",
        )
    assert ei.value.code == 40008
    assert "尚无已生效的公益身份" in ei.value.message


@pytest.mark.asyncio
async def test_建单公益与券互斥(db_session, sb):
    from app.services.customer_service import OrderCreateService

    await SocialBenefitService.pos_verify(
        db_session,
        customer_id=sb["c1"].id,
        identity_code="SANITATION",
        store_id=sb["store1"].id,
        operator_user_id=1,
    )
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await OrderCreateService.create_order(
            db_session,
            store_id=sb["store1"].id,
            table_token=None,
            customer_id=sb["c1"].id,
            channel="H5",
            dining_type="TAKEAWAY",
            need_pack=True,
            items=[{"product_id": sb["prod"].id, "quantity": 1, "option_ids": []}],
            use_social_benefit=True,
            customer_coupon_id=999001,
            idempotency_key="sb-exclusive-coupon-1",
        )
    assert ei.value.code == 40008
    assert "不可与优惠券同享" in ei.value.message

    order = await OrderCreateService.create_order(
        db_session,
        store_id=sb["store1"].id,
        table_token=None,
        customer_id=sb["c1"].id,
        channel="H5",
        dining_type="TAKEAWAY",
        need_pack=True,
        items=[{"product_id": sb["prod"].id, "quantity": 1, "option_ids": []}],
        use_social_benefit=True,
        idempotency_key="sb-social-only-1",
    )
    await db_session.commit()
    assert money(order.discount_amount) == money("4.00")
    assert money(order.coupon_discount) == money("0.00")
    assert order.customer_coupon_id is None
    assert money(order.total_amount) == money("16.00")
    red = (
        await db_session.execute(
            select(CustomerSocialIdentity).where(
                CustomerSocialIdentity.customer_id == sb["c1"].id,
                CustomerSocialIdentity.identity_code == "SANITATION",
                CustomerSocialIdentity.status == "ACTIVE",
            )
        )
    ).scalar_one()
    assert red is not None
