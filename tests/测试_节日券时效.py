"""节日券模板发放窗 + 活动出窗作废未用券。"""
from datetime import date, timedelta
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
from app.services.coupon_service import CouponService
from app.services.marketing_service import MarketingService
from app.services.segment_service import SegmentService
from app.services.system_settings_service import SystemSettingsService
from app.utils import campaign_schedule as cs


async def _seed(db):
    store = Store(store_no="FEST01", name="节日时效测店", status=1)
    db.add(store)
    await db.flush()
    lv = MemberLevel(name="普通", min_points=0, discount_rate=Decimal("1.00"))
    db.add(lv)
    await db.flush()
    c = Customer(
        customer_no="C-FEST-1",
        display_name="节日客",
        member_status="MEMBER",
        account_status="ACTIVE",
        status=1,
        member_level_id=lv.id,
        registered_store_id=store.id,
    )
    db.add(c)
    tmpl = CouponTemplate(
        store_id=None,
        name="教师节满20减3测",
        coupon_type="FIXED",
        discount_value=Decimal("3.00"),
        min_order_amount=Decimal("20.00"),
        valid_days=5,
        status=1,
        schedule_mode="ANNUAL_MD",
        schedule_calendar="SOLAR",
        annual_start_month=9,
        annual_start_day=9,
        annual_end_month=9,
        annual_end_day=11,
    )
    db.add(tmpl)
    db.add(SystemSetting(setting_key="marketing_weekly_cap", setting_value="10"))
    await db.flush()
    SystemSettingsService.invalidate_cache_local()
    await SystemSettingsService._load_raw(db)
    await db.commit()
    return {"store": store, "customer": c, "tmpl": tmpl}


@pytest.mark.asyncio
async def test_festival_template_blocks_issue_outside_window(db_session, monkeypatch):
    seed = await _seed(db_session)
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2026, 10, 22))
    with pytest.raises(AppException) as ei:
        await CouponService.issue_by_template_id(
            db_session,
            customer_id=int(seed["customer"].id),
            template_id=int(seed["tmpl"].id),
        )
    assert ei.value.code == 40001
    assert "发放窗口" in str(ei.value.message) or "有效期" in str(ei.value.message)


@pytest.mark.asyncio
async def test_festival_template_allows_issue_inside_window(db_session, monkeypatch):
    seed = await _seed(db_session)
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2026, 9, 10))
    issued = await CouponService.issue_by_template_id(
        db_session,
        customer_id=int(seed["customer"].id),
        template_id=int(seed["tmpl"].id),
        client_request_id="fest-in-window-1",
    )
    assert len(issued) == 1
    assert issued[0].status == "AVAILABLE"


@pytest.mark.asyncio
async def test_annual_campaign_pause_expires_unused_coupons(db_session, monkeypatch):
    """RUNNING→出窗 PAUSED 时，本季 mkt: 未用券作废。"""
    seed = await _seed(db_session)
    in_day = date(2026, 9, 10)
    out_day = date(2026, 9, 12)
    monkeypatch.setattr(cs, "today_beijing", lambda: in_day)
    monkeypatch.setattr("app.services.marketing_service.today_beijing", lambda: in_day)

    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="teacher_seg",
        name="教师测",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="festival_teacher_ttl",
        name="教师节时效",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        schedule_mode="ANNUAL_MD",
        schedule_calendar="SOLAR",
        annual_start_month=9,
        annual_start_day=9,
        annual_end_month=9,
        annual_end_day=11,
    )
    pub = await MarketingService.publish(db_session, int(camp.id))
    assert pub["campaign"]["status"] == "RUNNING"
    await db_session.commit()

    stats = await MarketingService.process_pending_tasks(db_session, limit=20)
    await db_session.commit()
    assert stats["success"] >= 1

    avail = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(
                CustomerCoupon.customer_id == seed["customer"].id,
                CustomerCoupon.status == "AVAILABLE",
            )
        )
        or 0
    )
    assert avail >= 1

    # 出窗
    monkeypatch.setattr(cs, "today_beijing", lambda: out_day)
    monkeypatch.setattr("app.services.marketing_service.today_beijing", lambda: out_day)
    adv = await MarketingService.advance_campaign_lifecycle(db_session)
    await db_session.commit()
    assert adv["paused"] >= 1

    camp2 = await db_session.get(MarketingCampaign, int(camp.id))
    assert camp2.status == "PAUSED"

    expired = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(
                CustomerCoupon.customer_id == seed["customer"].id,
                CustomerCoupon.status == "EXPIRED",
                CustomerCoupon.status_reason == "campaign_window_end",
            )
        )
        or 0
    )
    assert expired >= 1


@pytest.mark.asyncio
async def test_cross_campaign_same_template_skipped(db_session, monkeypatch):
    """两活动共用同模板：第一活动发成功后，第二活动应对同顾客 SKIPPED。"""
    seed = await _seed(db_session)
    in_day = date(2026, 9, 10)
    monkeypatch.setattr(cs, "today_beijing", lambda: in_day)
    monkeypatch.setattr("app.services.marketing_service.today_beijing", lambda: in_day)

    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="teacher_seg_dup",
        name="教师防重测",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp_a = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="festival_teacher_a",
        name="教师节A",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        schedule_mode="ANNUAL_MD",
        schedule_calendar="SOLAR",
        annual_start_month=9,
        annual_start_day=9,
        annual_end_month=9,
        annual_end_day=11,
    )
    camp_b = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="festival_teacher_b",
        name="教师节B",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        schedule_mode="ANNUAL_MD",
        schedule_calendar="SOLAR",
        annual_start_month=9,
        annual_start_day=9,
        annual_end_month=9,
        annual_end_day=11,
    )
    await MarketingService.publish(db_session, int(camp_a.id))
    await MarketingService.publish(db_session, int(camp_b.id))
    await db_session.commit()

    stats = await MarketingService.process_pending_tasks(db_session, limit=50)
    await db_session.commit()
    assert stats["success"] >= 1
    assert stats["skipped"] >= 1

    avail = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(
                CustomerCoupon.customer_id == seed["customer"].id,
                CustomerCoupon.template_id == seed["tmpl"].id,
                CustomerCoupon.status == "AVAILABLE",
            )
        )
        or 0
    )
    assert avail == 1

    skipped_task = (
        await db_session.execute(
            select(MarketingTask).where(
                MarketingTask.campaign_id == int(camp_b.id),
                MarketingTask.customer_id == int(seed["customer"].id),
                MarketingTask.status == "SKIPPED",
            )
        )
    ).scalars().first()
    # A/B 谁先 claim 不定：至少一个 SKIPPED，且 reason 为防重
    if skipped_task is None:
        skipped_task = (
            await db_session.execute(
                select(MarketingTask).where(
                    MarketingTask.campaign_id == int(camp_a.id),
                    MarketingTask.customer_id == int(seed["customer"].id),
                    MarketingTask.status == "SKIPPED",
                )
            )
        ).scalars().first()
    assert skipped_task is not None
    assert (skipped_task.result_json or {}).get("reason") in (
        "template_held",
        "season_already_issued",
    )
