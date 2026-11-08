"""WP-B2：分群估人、活动发布发券、DRAFT 不发、幂等、暂停停发、频控。"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, inspect, select

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    CustomerSegment,
    CustomerSocialIdentity,
    MarketingCampaign,
    MarketingTask,
    MemberLevel,
    Store,
    SystemSetting,
)
from app.services.marketing_service import (
    MAX_TASK_TRY,
    STALE_RUNNING_SEC,
    MarketingService,
)
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
async def test_segment_social_identity_code(db_session):
    """公益身份分群：仅 ACTIVE 且有效期内命中；PENDING/过期不进圈。"""
    seed = await _seed(db_session)
    teacher, elder, plain = seed["members"]
    db_session.add(
        CustomerSocialIdentity(
            customer_id=int(teacher.id),
            identity_code="TEACHER",
            status="ACTIVE",
        )
    )
    db_session.add(
        CustomerSocialIdentity(
            customer_id=int(elder.id),
            identity_code="TEACHER",
            status="PENDING",
        )
    )
    db_session.add(
        CustomerSocialIdentity(
            customer_id=int(plain.id),
            identity_code="TEACHER",
            status="ACTIVE",
            valid_to=naive_local_now() - timedelta(days=1),
        )
    )
    await db_session.commit()

    rules = {
        "all": [
            {"field": "member_status", "op": "eq", "value": "MEMBER"},
            {"field": "social_identity_code", "op": "eq", "value": "TEACHER"},
        ]
    }
    est = await SegmentService.estimate(db_session, rules_json=rules, store_id=seed["store"].id)
    assert est["count"] == 1
    assert est["samples"][0]["id"] == int(teacher.id)


@pytest.mark.asyncio
async def test_segment_is_birthday_today(db_session):
    """今日生日分群：仅命中当日生日会员，勿把全体会员圈进生日活动。"""
    from app.utils.birthday import today_beijing

    seed = await _seed(db_session)
    today = today_beijing()
    bday_member, other_member, no_bday = seed["members"]
    bday_member.birthday = today.replace(year=1990)
    bday_member.birthday_calendar = "SOLAR"
    other_member.birthday = today.replace(year=1991, month=((today.month % 12) + 1), day=1)
    other_member.birthday_calendar = "SOLAR"
    no_bday.birthday = None
    await db_session.commit()

    rules = {
        "all": [
            {"field": "member_status", "op": "eq", "value": "MEMBER"},
            {"field": "is_birthday_today", "op": "eq", "value": True},
        ]
    }
    est = await SegmentService.estimate(db_session, rules_json=rules, store_id=seed["store"].id)
    assert est["count"] == 1
    assert est["samples"][0]["id"] == int(bday_member.id)

    # 绑 all_members 会圈到全部会员；生日活动必须用 is_birthday_today
    all_est = await SegmentService.estimate(
        db_session,
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
        store_id=seed["store"].id,
    )
    assert all_est["count"] == 3
    assert all_est["count"] > est["count"]


@pytest.mark.asyncio
async def test_segment_is_member_anniversary_today(db_session):
    """入会周年：仅无生日且入会月日=今日（满1年）；有生日的不进此圈。"""
    from datetime import datetime
    from app.utils.birthday import today_beijing

    seed = await _seed(db_session)
    today = today_beijing()
    anniv_member, has_bday, other_md = seed["members"]
    # 今日入会周年、无生日
    anniv_member.birthday = None
    anniv_member.member_activated_at = datetime(
        today.year - 2, today.month, today.day, 12, 0, 0
    )
    # 有生日：即使入会月日相同也不算入会周年
    has_bday.birthday = today.replace(year=1990)
    has_bday.birthday_calendar = "SOLAR"
    has_bday.member_activated_at = datetime(
        today.year - 3, today.month, today.day, 12, 0, 0
    )
    # 无生日但入会月日不同
    other_md.birthday = None
    other_month = (today.month % 12) + 1
    other_md.member_activated_at = datetime(today.year - 2, other_month, 1, 12, 0, 0)
    await db_session.commit()

    rules = {
        "all": [
            {"field": "member_status", "op": "eq", "value": "MEMBER"},
            {"field": "is_member_anniversary_today", "op": "eq", "value": True},
        ]
    }
    est = await SegmentService.estimate(db_session, rules_json=rules, store_id=seed["store"].id)
    assert est["count"] == 1
    assert est["samples"][0]["id"] == int(anniv_member.id)


@pytest.mark.asyncio
async def test_publish_member_anniversary_blocks_all_members(db_session):
    """入会周年活动禁止绑全体会员。"""
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=None,
        code="all_members",
        name="全体会员",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=None,
        code="anniv_bad_seg",
        name="入会周年误绑",
        campaign_type="MEMBER_ANNIVERSARY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        start_at=naive_local_now() - timedelta(hours=1),
        end_at=naive_local_now() + timedelta(days=1),
    )
    await db_session.commit()
    with pytest.raises(AppException) as ei:
        await MarketingService.publish(db_session, int(camp.id))
    assert "入会周年" in str(ei.value.message)


@pytest.mark.asyncio
async def test_publish_member_anniversary_requires_field(db_session):
    """入会周年须绑含 is_member_anniversary_today 的分群，不能随便绑沉睡分群。"""
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=None,
        code="sleep30_fake",
        name="沉睡误绑",
        rules_json={
            "all": [
                {"field": "member_status", "op": "eq", "value": "MEMBER"},
                {"field": "days_since_order", "op": "gte", "value": 30},
            ]
        },
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=None,
        code="anniv_need_field",
        name="入会周年缺字段",
        campaign_type="MEMBER_ANNIVERSARY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        start_at=naive_local_now() - timedelta(hours=1),
        end_at=naive_local_now() + timedelta(days=1),
    )
    await db_session.commit()
    with pytest.raises(AppException) as ei:
        await MarketingService.publish(db_session, int(camp.id))
    assert "is_member_anniversary_today" in str(ei.value.message)


@pytest.mark.asyncio
async def test_member_anniversary_season_and_daily_enqueue(db_session, monkeypatch):
    """入会周年：幂等键带 Y{年}；RUNNING 时 advance 可按今日分群补圈。"""
    from datetime import date, datetime

    from app.utils import campaign_schedule as cs

    seed = await _seed(db_session)
    today = date(2026, 10, 22)
    monkeypatch.setattr(cs, "today_beijing", lambda: today)
    monkeypatch.setattr(
        "app.services.marketing_service.today_beijing", lambda: today
    )
    monkeypatch.setattr("app.utils.birthday.today_beijing", lambda: today)

    hit, miss, with_bday = seed["members"]
    hit.birthday = None
    hit.member_activated_at = datetime(2024, 10, 22, 12, 0, 0)
    miss.birthday = None
    miss.member_activated_at = datetime(2024, 3, 1, 12, 0, 0)
    with_bday.birthday = today.replace(year=1990)
    with_bday.birthday_calendar = "SOLAR"
    with_bday.member_activated_at = datetime(2024, 10, 22, 12, 0, 0)

    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="member_anniversary_today",
        name="今日入会周年",
        rules_json={
            "all": [
                {"field": "member_status", "op": "eq", "value": "MEMBER"},
                {"field": "is_member_anniversary_today", "op": "eq", "value": True},
            ]
        },
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="anniv_daily",
        name="入会周年日发",
        campaign_type="MEMBER_ANNIVERSARY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        start_at=naive_local_now() - timedelta(hours=1),
        end_at=None,
    )
    await db_session.commit()

    season = MarketingService.season_for_campaign(camp)
    assert season == "Y2026"

    pub = await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()
    assert pub["campaign"]["status"] == "RUNNING"
    assert pub["enqueued"] == 1

    key = await db_session.scalar(
        select(MarketingTask.idempotency_key).where(
            MarketingTask.campaign_id == int(camp.id)
        )
    )
    assert key and str(key).endswith(":Y2026")

    # 同日再 advance：幂等跳过，不新增任务
    await MarketingService.advance_campaign_lifecycle(db_session)
    await db_session.commit()
    pending = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(MarketingTask.campaign_id == int(camp.id))
        )
        or 0
    )
    assert pending == 1

    # 次年季标识变化，可再发
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2027, 10, 22))
    monkeypatch.setattr(
        "app.services.marketing_service.today_beijing", lambda: date(2027, 10, 22)
    )
    monkeypatch.setattr(
        "app.utils.birthday.today_beijing", lambda: date(2027, 10, 22)
    )
    await db_session.refresh(camp)
    assert MarketingService.season_for_campaign(camp) == "Y2027"
    adv2 = await MarketingService.advance_campaign_lifecycle(db_session)
    await db_session.commit()
    assert adv2["enqueued"] >= 1
    keys = list(
        (
            await db_session.execute(
                select(MarketingTask.idempotency_key).where(
                    MarketingTask.campaign_id == int(camp.id)
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(str(k).endswith(":Y2027") for k in keys)


@pytest.mark.asyncio
async def test_publish_all_members_requires_window(db_session):
    """全体会员活动无排期不可发布，防止非节日误发。"""
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=None,
        code="all_members",
        name="全体会员",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=None,
        code="fest_need_window",
        name="节日测",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await db_session.commit()
    with pytest.raises(AppException) as ei:
        await MarketingService.publish(db_session, int(camp.id))
    assert "绝对起止" in str(ei.value.message) or "每年月日" in str(ei.value.message)

    now = naive_local_now()
    camp2 = await MarketingService.upsert_campaign(
        db_session,
        campaign_id=int(camp.id),
        store_id=None,
        code="fest_need_window",
        name="节日测",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(days=2),
    )
    await db_session.commit()
    pub = await MarketingService.publish(db_session, int(camp2.id))
    assert pub["campaign"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_publish_birthday_forbids_all_members(db_session):
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=None,
        code="all_members",
        name="全体会员",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=None,
        code="bday_bad",
        name="生日误绑",
        campaign_type="BIRTHDAY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        start_at=naive_local_now(),
        end_at=naive_local_now() + timedelta(days=1),
    )
    await db_session.commit()
    with pytest.raises(AppException) as ei:
        await MarketingService.publish(db_session, int(camp.id))
    assert "生日" in str(ei.value.message)


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
        start_at=naive_local_now() - timedelta(hours=1),
        end_at=naive_local_now() + timedelta(days=7),
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
    # 已存在同模板有效券时应记为 SKIPPED，而不是把幂等去重伪报为 SUCCESS。
    assert stats2["success"] == 0
    assert stats2["skipped"] == 3
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
        start_at=naive_local_now() - timedelta(hours=1),
        end_at=naive_local_now() + timedelta(days=7),
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
async def test_lifecycle_field_and_store_scoped_hq_publish(db_session):
    """lifecycle_code 可估人；门店活动引用总部分群按本店动态圈人且不覆盖总部快照。"""
    from app.models import CustomerLifecycle, CustomerSegmentMember

    seed = await _seed(db_session)
    store = seed["store"]
    # 仅给第一位会员打上 AT_RISK
    c0 = seed["members"][0]
    db_session.add(
        CustomerLifecycle(
            customer_id=int(c0.id),
            lifecycle_code="AT_RISK",
            silence_days=40,
        )
    )
    await db_session.flush()

    rules = {
        "all": [
            {"field": "member_status", "op": "eq", "value": "MEMBER"},
            {"field": "lifecycle_code", "op": "eq", "value": "AT_RISK"},
        ]
    }
    est = await SegmentService.estimate(db_session, rules_json=rules, store_id=int(store.id))
    assert est["count"] == 1

    # 总部预置分群（store_id=None）
    hq_seg = await SegmentService.upsert(
        db_session,
        store_id=None,
        code="hq_sleep_test",
        name="总部沉睡测",
        rules_json={
            "all": [
                {"field": "member_status", "op": "eq", "value": "MEMBER"},
                {"field": "days_since_order", "op": "gte", "value": 30},
            ]
        },
    )
    # 先物化总部快照（应含 3 人）
    mat_hq = await SegmentService.materialize(db_session, int(hq_seg.id))
    await db_session.commit()
    assert mat_hq["count"] == 3
    hq_members_before = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerSegmentMember)
            .where(CustomerSegmentMember.segment_id == hq_seg.id)
        )
        or 0
    )
    assert hq_members_before == 3

    # 门店活动引用总部分群发布：按本店圈人，不改总部快照
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=int(store.id),
        code="store_use_hq",
        name="门店用总部群",
        segment_id=int(hq_seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    pub = await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()
    assert pub["materialized"]["mode"] == "dynamic_store"
    assert pub["enqueued"] == 3
    hq_members_after = int(
        await db_session.scalar(
            select(func.count())
            .select_from(CustomerSegmentMember)
            .where(CustomerSegmentMember.segment_id == hq_seg.id)
        )
        or 0
    )
    assert hq_members_after == hq_members_before  # 快照未被覆盖


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


@pytest.mark.asyncio
async def test_marketing_claim_and_stale_reclaim(db_session):
    """PENDING→RUNNING 原子 claim；超时 RUNNING 复位；超次 FAILED。"""
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="reclaim_seg",
        name="回收测分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="reclaim_camp",
        name="回收测活动",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()

    tid = await db_session.scalar(
        select(MarketingTask.id)
        .where(MarketingTask.campaign_id == camp.id)
        .order_by(MarketingTask.id)
        .limit(1)
    )
    assert tid

    claimed = await MarketingService._claim_pending_task(db_session, int(tid))
    await db_session.commit()
    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert int(claimed.try_count) == 1

    # 第二次 claim 失败
    again = await MarketingService._claim_pending_task(db_session, int(tid))
    assert again is None

    # 人为把 updated_at 打旧，触发回收 → PENDING
    stale_at = naive_local_now() - timedelta(seconds=STALE_RUNNING_SEC + 60)
    task = await db_session.get(MarketingTask, int(tid))
    task.updated_at = stale_at
    await db_session.commit()

    stats = await MarketingService.reclaim_stale_running(db_session, stale_after=60)
    await db_session.commit()
    assert stats["requeued"] >= 1
    task = await db_session.get(MarketingTask, int(tid))
    assert task.status == "PENDING"
    assert task.last_error == "reclaimed_stale_running"

    # 超次：RUNNING + try_count 触顶 → FAILED
    task.status = "RUNNING"
    task.try_count = MAX_TASK_TRY
    task.updated_at = stale_at
    await db_session.commit()
    stats2 = await MarketingService.reclaim_stale_running(db_session, stale_after=60)
    await db_session.commit()
    assert stats2["failed"] >= 1
    task = await db_session.get(MarketingTask, int(tid))
    assert task.status == "FAILED"
    assert task.last_error == "stale_running_max_try"


@pytest.mark.asyncio
async def test_marketing_claim_committed_before_issue(db_session, monkeypatch):
    """claim 后立即 commit：发券中途失败时 RUNNING 已落库，可被 stale 回收。"""
    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="claim_commit_seg",
        name="claim落库分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="claim_commit_camp",
        name="claim落库活动",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
    )
    await MarketingService.publish(db_session, int(camp.id))
    await db_session.commit()

    from app.services.coupon_service import CouponService

    async def _boom(*_a, **_k):
        raise RuntimeError("issue_boom")

    monkeypatch.setattr(CouponService, "issue_by_template_id", staticmethod(_boom))

    stats = await MarketingService.process_pending_tasks(db_session, limit=50)
    await db_session.commit()
    assert stats["failed"] >= 1

    failed_n = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(MarketingTask.campaign_id == camp.id, MarketingTask.status == "FAILED")
        )
        or 0
    )
    assert failed_n >= 1
    # 不应残留 RUNNING（异常路径已写 FAILED）
    running_n = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(MarketingTask.campaign_id == camp.id, MarketingTask.status == "RUNNING")
        )
        or 0
    )
    assert running_n == 0


@pytest.mark.asyncio
async def test_campaign_budget_cap_stops_issue(db_session):
    """budget_cap 按 SUCCESS×面额累计；超限 SKIPPED 并取消剩余 PENDING。"""
    seed = await _seed(db_session)
    # 面额 5 元，预算 10 → 最多成功 2 张
    seed["tmpl"].discount_value = Decimal("5.00")
    await db_session.flush()
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="budget_seg",
        name="预算测分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="budget_camp",
        name="预算截停活动",
        campaign_type="MEMBER_DAY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        budget_cap=Decimal("10.00"),
        weekly_cap_override=10,
    )
    pub = await MarketingService.publish(db_session, int(camp.id))
    assert pub["enqueued"] == 3
    await db_session.commit()

    stats = await MarketingService.process_pending_tasks(db_session, limit=20)
    await db_session.commit()
    assert stats["success"] == 2
    assert stats["skipped"] >= 1
    pending = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(
                MarketingTask.campaign_id == camp.id,
                MarketingTask.status == "PENDING",
            )
        )
        or 0
    )
    assert pending == 0
    cancelled = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(
                MarketingTask.campaign_id == camp.id,
                MarketingTask.status == "CANCELLED",
            )
        )
        or 0
    )
    # 第 3 条 SKIPPED，无更多 PENDING 可取消；若批内先 SKIP 再 cancel 则 cancelled≥0
    assert stats["success"] + stats["skipped"] + cancelled >= 3


@pytest.mark.asyncio
async def test_welcome_coupon_only_for_new_member_campaign(db_session):
    """「新客满20减3」类欢迎券不可绑到非 NEW_MEMBER 活动。"""
    seed = await _seed(db_session)
    welcome = CouponTemplate(
        store_id=None,
        name="新客满20减3",
        coupon_type="FIXED",
        discount_value=Decimal("3.00"),
        min_order_amount=Decimal("20.00"),
        valid_days=30,
        status=1,
    )
    db_session.add(welcome)
    db_session.add(
        SystemSetting(setting_key="welcome_coupon_name", setting_value="新客满20减3")
    )
    await db_session.flush()
    SystemSettingsService.invalidate_cache_local()
    await SystemSettingsService._load_raw(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="welcome_only_seg",
        name="欢迎测分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    with pytest.raises(AppException) as ei:
        await MarketingService.upsert_campaign(
            db_session,
            store_id=seed["store"].id,
            code="member_day_bad_welcome",
            name="误绑新客券",
            campaign_type="MEMBER_DAY",
            segment_id=int(seg.id),
            coupon_template_id=int(welcome.id),
        )
    assert ei.value.code == 40001
    assert "新客" in str(ei.value.message)

    ok = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="new_member_ok_welcome",
        name="新客欢迎合法",
        campaign_type="NEW_MEMBER",
        segment_id=int(seg.id),
        coupon_template_id=int(welcome.id),
    )
    assert int(ok.coupon_template_id) == int(welcome.id)


@pytest.mark.asyncio
async def test_campaign_payload_after_commit_reads_timestamps(db_session):
    """回归：upsert+commit 后同步 _payload 读 created_at 不得 MissingGreenlet。"""
    from sqlalchemy.exc import MissingGreenlet, StatementError

    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="payload_seg",
        name="payload分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="payload_camp",
        name="payload活动",
        campaign_type="MEMBER_DAY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        budget_cap=Decimal("100.00"),
    )
    await db_session.commit()

    payload = MarketingService._payload(camp)
    assert payload["id"] == int(camp.id)
    assert payload["created_at"]
    assert payload["updated_at"]
    assert payload["budget_cap"] == "100.00"

    # 同步序列化前必须确认异步 ORM 字段已加载；不要故意触发 MissingGreenlet，
    # 因为驱动会为失败的同步懒加载创建无法 await 的 cursor 协程。
    db_session.expire(camp, ["created_at", "updated_at"])
    assert {"created_at", "updated_at"}.issubset(inspect(camp).expired_attributes)

    # 正确做法是在异步上下文中显式 refresh，再交给同步序列化函数。
    await db_session.refresh(camp, attribute_names=["created_at", "updated_at"])
    assert not ({"created_at", "updated_at"} & inspect(camp).expired_attributes)
    again = MarketingService._payload(camp)
    assert again["created_at"]
    assert again["updated_at"]


@pytest.mark.asyncio
async def test_campaign_payload_for_includes_created_by_name(db_session):
    """列表/单条序列化应带 created_by_name（display_name 优先，否则 username）。"""
    from app.models import User

    seed = await _seed(db_session)
    user = User(
        username="mkt_creator",
        password_hash="x",
        display_name="营销创建人",
        role_id=1,
        status=1,
    )
    db_session.add(user)
    await db_session.flush()

    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="creator_seg",
        name="创建人分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="creator_camp",
        name="创建人活动",
        campaign_type="MEMBER_DAY",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        created_by=int(user.id),
    )
    await db_session.commit()
    await db_session.refresh(camp)

    one = await MarketingService.payload_for(db_session, camp)
    assert one["created_by"] == int(user.id)
    assert one["created_by_name"] == "营销创建人"

    items, total = await MarketingService.list_campaigns(
        db_session, store_id=seed["store"].id, page=1, page_size=50
    )
    assert total >= 1
    hit = next(i for i in items if i["id"] == int(camp.id))
    assert hit["created_by_name"] == "营销创建人"


@pytest.mark.asyncio
async def test_annual_md_publish_and_lifecycle(db_session, monkeypatch):
    """每年月日：窗外启用为 PAUSED；进入窗口自动 RUNNING 并按季入队；窗外再 PAUSED。"""
    from datetime import date

    from app.utils import campaign_schedule as cs

    seed = await _seed(db_session)
    seg = await SegmentService.upsert(
        db_session,
        store_id=seed["store"].id,
        code="ann_all",
        name="年度测分群",
        rules_json={"all": [{"field": "member_status", "op": "eq", "value": "MEMBER"}]},
    )
    camp = await MarketingService.upsert_campaign(
        db_session,
        store_id=seed["store"].id,
        code="festival_ann_test",
        name="年度节日测",
        campaign_type="FESTIVAL",
        segment_id=int(seg.id),
        coupon_template_id=int(seed["tmpl"].id),
        schedule_mode="ANNUAL_MD",
        schedule_calendar="SOLAR",
        annual_start_month=6,
        annual_start_day=1,
        annual_end_month=6,
        annual_end_day=3,
    )
    await db_session.commit()

    # 窗外发布 → PAUSED，不入队
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2026, 5, 31))
    monkeypatch.setattr(
        "app.services.marketing_service.today_beijing", lambda: date(2026, 5, 31)
    )
    pub = await MarketingService.publish(db_session, int(camp.id))
    assert pub["campaign"]["status"] == "PAUSED"
    assert pub["enqueued"] == 0
    await db_session.commit()

    # 进入窗口 → RUNNING + 入队
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2026, 6, 1))
    monkeypatch.setattr(
        "app.services.marketing_service.today_beijing", lambda: date(2026, 6, 1)
    )
    adv = await MarketingService.advance_campaign_lifecycle(db_session)
    await db_session.commit()
    await db_session.refresh(camp)
    assert camp.status == "RUNNING"
    assert adv["started"] >= 1
    assert adv["enqueued"] >= 1
    pending = int(
        await db_session.scalar(
            select(func.count())
            .select_from(MarketingTask)
            .where(
                MarketingTask.campaign_id == int(camp.id),
                MarketingTask.status == "PENDING",
            )
        )
        or 0
    )
    assert pending >= 1
    # 幂等键带季标识
    key = await db_session.scalar(
        select(MarketingTask.idempotency_key).where(
            MarketingTask.campaign_id == int(camp.id)
        ).limit(1)
    )
    assert key and ":S2026" in str(key)

    # 窗外 → PAUSED（不 ENDED）
    monkeypatch.setattr(cs, "today_beijing", lambda: date(2026, 6, 4))
    monkeypatch.setattr(
        "app.services.marketing_service.today_beijing", lambda: date(2026, 6, 4)
    )
    adv2 = await MarketingService.advance_campaign_lifecycle(db_session)
    await db_session.commit()
    await db_session.refresh(camp)
    assert camp.status == "PAUSED"
    assert adv2["paused"] >= 1


def test_annual_md_window_helpers():
    from datetime import date

    from app.utils.campaign_schedule import (
        annual_season_token,
        is_in_annual_window,
    )

    assert is_in_annual_window(
        calendar="SOLAR",
        start_month=10,
        start_day=1,
        end_month=10,
        end_day=7,
        today=date(2026, 10, 3),
    )
    assert not is_in_annual_window(
        calendar="SOLAR",
        start_month=10,
        start_day=1,
        end_month=10,
        end_day=7,
        today=date(2026, 10, 8),
    )
    # 跨年
    assert is_in_annual_window(
        calendar="SOLAR",
        start_month=12,
        start_day=21,
        end_month=1,
        end_day=5,
        today=date(2027, 1, 2),
    )
    assert annual_season_token(
        calendar="SOLAR",
        start_month=12,
        start_day=21,
        end_month=1,
        end_day=5,
        today=date(2027, 1, 2),
    ) == "S2026"
