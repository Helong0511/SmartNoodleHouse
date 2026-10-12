"""WP-B4：服务卡脱敏、无证件图、常点/券/公益态。"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.response import naive_local_now
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    CustomerPreference,
    CustomerSocialIdentity,
    MemberLevel,
    SocialIdentityType,
    Store,
)
from app.services.service_card_service import ServiceCardService


@pytest.mark.asyncio
async def test_service_card_masked_no_credential_media(db_session):
    store = Store(store_no="SC01", name="服务卡店", status=1)
    db_session.add(store)
    await db_session.flush()
    lv = MemberLevel(name="金卡", min_points=100, discount_rate=Decimal("0.95"))
    db_session.add(lv)
    await db_session.flush()
    c = Customer(
        customer_no="C-SC-1",
        display_name="王叔",
        phone="13812345678",
        member_status="MEMBER",
        account_status="ACTIVE",
        status=1,
        member_level_id=lv.id,
        registered_store_id=store.id,
        points=120,
        balance_principal=Decimal("30.00"),
        last_order_at=naive_local_now() - timedelta(days=3),
        member_activated_at=naive_local_now() - timedelta(days=40),
    )
    db_session.add(c)
    await db_session.flush()
    db_session.add(
        SocialIdentityType(code="SANITATION", name="环卫工", sort_order=1, status=1)
    )
    db_session.add(
        CustomerSocialIdentity(
            customer_id=c.id,
            identity_code="SANITATION",
            store_id=store.id,
            status="ACTIVE",
            credential_note="工牌核验",
            credential_media_url="/uploads/secret-id.jpg",
        )
    )
    db_session.add(
        CustomerPreference(
            customer_id=c.id,
            category="辣度",
            option_code="mid",
            option_name="中辣",
            preference_score=Decimal("0.80"),
            order_count=5,
        )
    )
    db_session.add(
        CustomerPreference(
            customer_id=c.id,
            category="忌口",
            option_code="no_cilantro",
            option_name="不要香菜",
            preference_score=Decimal("0.10"),
            order_count=2,
        )
    )
    tmpl = CouponTemplate(
        name="服务卡测券",
        coupon_type="FIXED",
        discount_value=Decimal("3.00"),
        min_order_amount=Decimal("10.00"),
        valid_days=7,
        status=1,
    )
    db_session.add(tmpl)
    await db_session.flush()
    db_session.add(
        CustomerCoupon(
            customer_id=c.id,
            template_id=tmpl.id,
            coupon_no="CP-SC-1",
            status="AVAILABLE",
            expires_at=naive_local_now() + timedelta(days=5),
        )
    )
    await db_session.commit()

    card = await ServiceCardService.build(db_session, int(c.id), store_id=int(store.id))
    assert card["display_name"] == "王叔"
    assert card["phone_masked"] == "138****5678"
    assert "13812345678" not in str(card)
    assert card["level_name"] == "金卡"
    assert card["lifecycle"] == "ACTIVE"
    assert card["coupon_count"] == 1
    assert card["available_coupons"][0]["name"] == "服务卡测券"
    assert any(x["label"].startswith("辣度") for x in card["taste_likes"])
    assert any("忌口" in x["label"] or "香菜" in x["label"] for x in card["taste_aversions"])
    assert card["social_identities"]
    assert card["social_identities"][0]["identity_code"] == "SANITATION"
    assert "credential_media_url" not in card["social_identities"][0]
    assert "/uploads/secret-id.jpg" not in str(card)
    assert card["social_today_used"] is False
