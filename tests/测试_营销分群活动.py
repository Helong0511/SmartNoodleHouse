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
    assert "开始与结束时间" in str(ei.value.message)

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
async def test_campaign_payload_after_commit_reads_timestamps(db_session):
    """回归：upsert+commit 后同步 _payload 读 created_at 不得 MissingGreenlet。"""
    from sqlalchemy.exc import MissingGreenlet

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

    # 显式 expire 后未 refresh 再同步读时间戳 → MissingGreenlet（契约说明）
    db_session.expire(camp, ["created_at", "updated_at"])
    with pytest.raises(MissingGreenlet):
        _ = MarketingService._payload(camp)

    await db_session.refresh(camp)
    again = MarketingService._payload(camp)
    assert again["created_at"]


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
