"""WP-C1：券优先归因、退款冲减、报表口径。"""
from decimal import Decimal

import pytest

from app.models import (
    CampaignAttribution,
    CouponTemplate,
    Customer,
    CustomerCoupon,
    MarketingCampaign,
    MemberLevel,
    Order,
    Store,
)
from app.core.response import naive_local_now
from app.services.attribution_service import AttributionService
from app.services.marketing_service import MarketingService


@pytest.mark.asyncio
async def test_attribute_from_mkt_coupon_and_reverse(db_session):
    store = Store(store_no="ATTR01", name="归因店", status=1)
    db_session.add(store)
    await db_session.flush()
    lv = MemberLevel(name="普", min_points=0, discount_rate=Decimal("1"))
    db_session.add(lv)
    await db_session.flush()
    c = Customer(
        customer_no="C-ATTR-1",
        member_status="MEMBER",
        account_status="ACTIVE",
        status=1,
        member_level_id=lv.id,
        registered_store_id=store.id,
    )
    db_session.add(c)
    await db_session.flush()
    tmpl = CouponTemplate(
        name="归因券",
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("10"),
        valid_days=7,
        status=1,
    )
    db_session.add(tmpl)
    await db_session.flush()
    camp = MarketingCampaign(
        store_id=store.id,
        code="attr_camp",
        name="归因活动",
        status="RUNNING",
        coupon_template_id=tmpl.id,
    )
    db_session.add(camp)
    await db_session.flush()
    cc = CustomerCoupon(
        customer_id=c.id,
        template_id=tmpl.id,
        coupon_no="CP-ATTR-1",
        status="USED",
        expires_at=naive_local_now(),
        client_request_id=f"mkt:{camp.id}:{c.id}:{tmpl.id}",
    )
    db_session.add(cc)
    await db_session.flush()
    order = Order(
        order_no="ORD-ATTR-1",
        customer_id=c.id,
        store_id=store.id,
        status="PAID",
        payment_status="PAID",
        total_amount=Decimal("30.00"),
        customer_coupon_id=cc.id,
        coupon_discount=Decimal("5.00"),
    )
    db_session.add(order)
    await db_session.commit()

    row = await AttributionService.attribute_paid_order(db_session, int(order.id))
    await db_session.commit()
    assert row is not None
    assert int(row.campaign_id) == int(camp.id)
    assert row.attribution_type == "COUPON"
    assert str(row.subsidy_amount) == "5.00"

    # 幂等
    row2 = await AttributionService.attribute_paid_order(db_session, int(order.id))
    assert int(row2.id) == int(row.id)

    n = await AttributionService.reverse_for_order(db_session, int(order.id))
    await db_session.commit()
    assert n == 1
    assert (await db_session.get(CampaignAttribution, row.id)).status == "REVERSED"

    report = await MarketingService.report_overview(db_session, store_id=int(store.id))
    assert any(i["campaign_id"] == int(camp.id) for i in report["items"])
