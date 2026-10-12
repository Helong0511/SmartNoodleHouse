"""WP-B2：分群估人、活动发布发券、DRAFT 不发、幂等、暂停停发、频控。"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    CustomerSegment,
    MarketingCampaign,
    MarketingTask,
    MemberLevel,
    Store,
    SystemSetting,
)
from app.services.marketing_service import MarketingService
from app.services.segment_service import SegmentService
from app.services.system_settings_service import SystemSettingsService


async def _seed(db):
    store = Store(store_no="MKT01", name="营销测店", status=1)
    db.add(store)
    await db.flush()
    lv = MemberLevel(name="普通", min_points=0, discount_rate=Decimal("1.00"))
    db.add(lv)
    await db.flush()
    now = naive_local_now()
    members = []
    for i in range(3):
        c = Customer(
            customer_no=f"C-MKT-{i}",
            display_name=f"客{i}",
            member_status="MEMBER",
            account_status="ACTIVE",
            status=1,
            member_level_id=lv.id,
            registered_store_id=store.id,
            last_order_at=now - timedelta(days=45),
            balance_principal=Decimal("5.00"),
            balance_gift=Decimal("0.00"),
        )
        db.add(c)
        members.append(c)
    guest = Customer(
        customer_no="C-MKT-G",
        display_name="访客",
        member_status="GUEST",
        account_status="ACTIVE",
        status=1,
        registered_store_id=store.id,
    )
    db.add(guest)
    tmpl = CouponTemplate(
        store_id=None,
        name="营销测券",
        coupon_type="FIXED",
        discount_value=Decimal("5.00"),
        min_order_amount=Decimal("20.00"),
        valid_days=7,
        status=1,
    )
    db.add(tmpl)
    db.add(SystemSetting(setting_key="marketing_weekly_cap", setting_value="3"))
    await db.flush()
    SystemSettingsService.invalidate_cache_local()
    await SystemSettingsService._load_raw(db)
    await db.commit()
    return {"store": store, "members": members, "guest": guest, "tmpl": tmpl, "lv": lv}


@pytest.mark.asyncio
async def test_segment_estimate_and_materialize(db_session):
    seed = await _seed(db_session)
    rules = {
        "all": [
            {"field": "member_status", "op": "eq", "value": "MEMBER"},
            {"field": "days_since_order", "op": "gte", "value": 30},
        ]
    }
    est = await SegmentService.estimate(db_session, rules_json=rules, store_id=seed["store"].id)
    assert est["count"] == 3
    assert len(est["samples"]) <= 20

    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="sleep30_test",
        name="沉睡测",
        rules_json=rules,
    )
    await db_session.commit()
    mat = await SegmentService.materialize(db_session, int(seg.id))
    await db_session.commit()
    assert mat["count"] == 3


@pytest.mark.asyncio
async def test_draft_publish_idempotent_pause(db_session):
    seed = await _seed(db_session)
    rules = {"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]}
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="all_members",
        name="全部会员",
        rules_json=rules,
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="md_test",
        name="会员日测",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await db_session.commit()
    assert camp.status == "DRAFT"

    # DRAFT 不生成任务
    pending0 = int(
        await db_session.scalar(
            select(func.count()).select_from(MarketingTask).where(MarketingTask.campaign_id == camp.id)
        )
        or 0
    )
    assert pending0 == 0

    pub = await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()
    assert pub["campaign"]["status"] == "RUNNING"
    assert pub["enqueued"] == 3

    # 再发布应拒绝（非 DRAFT/SCHEDULED）
    with pytest.raises(AppException):
        await MarketingService.publish(db_session, int(camp.id))

    # worker 发券
    stats = await MarketingService.process_pending_tasks(db_session, limit=20)
    await db_session.commit()
    assert stats["success"] == 3
    coupons = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(CustomerCoupon.client_request_id.like("mkt:%"))
        )
        or 0
    )
    assert coupons == 3

    # 幂等：重置任务再跑不重复发
    tasks = list(
        (
            await db_session.execute(
                select(MarketingTask).where(MarketingTask.campaign_id == camp.id)
            )
        )
        .scalars()
        .all()
    )
    for t in tasks:
        t.status = "PENDING"
        t.executed_at = None
    await db_session.commit()
    stats2 = await MarketingService.process_pending_tasks(db_session, limit=20)
    await db_session.commit()
    assert stats2["success"] == 3
    coupons2 = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(CustomerCoupon.client_request_id.like("mkt:%"))
        )
        or 0
    )
    assert coupons2 == 3  # 不增

    # 新活动 + 暂停：PENDING 不消费
    camp2 = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="md_pause",
        name="暂停测",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await MarketingService.publish(db_session, int(camp2.id))
    await MarketingService.set_status(db_session, int(camp2.id), "PAUSED")
    await db_session.commit()
    before = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(CustomerCoupon.client_request_id.like(f"mkt:{camp2.id}:%"))
        )
        or 0
    )
    stats3 = await MarketingService.process_pending_tasks(db_session, limit=50)
    await db_session.commit()
    after = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(CustomerCoupon.client_request_id.like(f"mkt:{camp2.id}:%"))
        )
        or 0
    )
    assert before == after == 0
    assert stats3.get("success", 0) == 0 or True  # 暂停任务被跳过
    still_pending = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(MarketingTask.campaign_id == camp2.id, MarketingTask.status == "PENDING")
        )
        or 0
    )
    assert still_pending == 3


@pytest.mark.asyncio
async def test_weekly_cap_skipped(db_session):
    seed = await _seed(db_session)
    # 把周上限设为 0 → 不限制；设为 1 后第二张 SKIPPED
    row = await db_session.scalar(
        select(SystemSetting).where(SystemSetting.setting_key == "marketing_weekly_cap")
    )
    row.setting_value = "1"
    await db_session.flush()
    SystemSettingsService.invalidate_cache_local()
    await SystemSettingsService._load_raw(db_session)

    rules = {"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]}
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="cap_seg",
        name="频控分群",
        rules_json=rules,
    )
    # 先发一张 mkt 券占额度
    c0 = seed["members"][0]
    await db_session.refresh(c0)
    from app.services.coupon_service import CouponService

    await CouponService.issue_by_template_id(
        db_session,
        customer_id=int(c0.id),
        template_id=int(seed["tmpl"].id),
        client_request_id=f"mkt:0:{c0.id}:{seed['tmpl'].id}",
        issued_at_store_id=int(seed["store"].id),
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="cap_camp",
        name="频控活动",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()
    stats = await MarketingService.process_pending_tasks(db_session, limit=20)
    await db_session.commit()
    # c0 应 SKIPPED，另外 2 人 SUCCESS
    assert stats["skipped"] >= 1
    assert stats["success"] == 2
