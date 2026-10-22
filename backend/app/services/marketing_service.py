"""营销活动：发布入队 ISSUE_COUPON、频控、任务消费。

年度节日（schedule_mode=ANNUAL_MD）按公历/农历月日自动 RUNNING↔PAUSED，
无需每年重录起止日期；发券幂等键带季标识，次年可再发。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    CustomerSegment,
    MarketingCampaign,
    MarketingRule,
    MarketingTask,
    User,
)
from app.services.coupon_service import CouponService
from app.services.scope_override import hq_override_warning, merge_store_over_hq
from app.services.segment_service import SegmentService
from app.services.system_settings_service import SystemSettingsService
from app.utils.birthday import today_beijing
from app.utils.campaign_schedule import (
    SCHEDULE_ABSOLUTE,
    SCHEDULE_ANNUAL_MD,
    annual_season_token,
    campaign_in_schedule_window,
    format_annual_label,
    normalize_schedule_calendar,
    normalize_schedule_mode,
)

logger = logging.getLogger(__name__)

SETTING_WEEKLY_CAP = "marketing_weekly_cap"
DEFAULT_WEEKLY_CAP = 3
EDITABLE_STATUSES = frozenset({"DRAFT"})
PUBLISHABLE_STATUSES = frozenset({"DRAFT", "SCHEDULED"})
# 年度活动启用后在 PAUSED 也可被调度重新拉起（不必每年重新发布）
ANNUAL_ACTIVE_STATUSES = frozenset({"SCHEDULED", "RUNNING", "PAUSED"})
WORKER_BATCH = 50
# claim 后进程崩溃且事务已提交时，RUNNING 超过此时长视为残留
STALE_RUNNING_SEC = 300
# 同一任务 claim/回收超过该次数后标 FAILED，避免无限重试
MAX_TASK_TRY = 8


class MarketingService:
    @staticmethod
    def idem_key(
        campaign_id: int,
        customer_id: int,
        template_id: int,
        *,
        season: str | None = None,
    ) -> str:
        base = f"mkt:{int(campaign_id)}:{int(customer_id)}:{int(template_id)}"
        if season:
            return f"{base}:{season}"
        return base

    # 按「今日命中顾客」每日补圈的活动类型（生日 / 入会周年）
    DAILY_AUDIENCE_TYPES = frozenset({"BIRTHDAY", "MEMBER_ANNIVERSARY"})

    @classmethod
    def season_for_campaign(cls, camp: MarketingCampaign) -> str | None:
        """发券幂等季标识。

        - ANNUAL_MD：公历/农历节日季（如 S2026）
        - BIRTHDAY / MEMBER_ANNIVERSARY：按北京时间自然年 ``Y{年}``，同年不重复、次年可再发
        """
        ctype = (camp.campaign_type or "").upper()
        if ctype in cls.DAILY_AUDIENCE_TYPES:
            return f"Y{today_beijing().year}"
        if normalize_schedule_mode(getattr(camp, "schedule_mode", None)) != SCHEDULE_ANNUAL_MD:
            return None
        return annual_season_token(
            calendar=getattr(camp, "schedule_calendar", None),
            start_month=getattr(camp, "annual_start_month", None),
            start_day=getattr(camp, "annual_start_day", None),
            end_month=getattr(camp, "annual_end_month", None),
            end_day=getattr(camp, "annual_end_day", None),
        )

    @classmethod
    def _normalize_annual_md(cls, month: int | None, day: int | None) -> tuple[int | None, int | None]:
        if month is None or day is None:
            return None, None
        try:
            m, d = int(month), int(day)
        except (TypeError, ValueError):
            raise AppException(40001, "年度月日须为整数") from None
        if m < 1 or m > 12 or d < 1 or d > 31:
            raise AppException(40001, "年度月日超出范围（月1-12，日1-31）")
        return m, d

    @classmethod
    async def weekly_cap(cls, db: AsyncSession, campaign: MarketingCampaign | None = None) -> int:
        if campaign is not None and campaign.weekly_cap_override is not None:
            return max(0, int(campaign.weekly_cap_override))
        raw = await SystemSettingsService.effective_setting(
            db, SETTING_WEEKLY_CAP, fallback=DEFAULT_WEEKLY_CAP
        )
        try:
            return max(0, int(str(raw or DEFAULT_WEEKLY_CAP).strip() or DEFAULT_WEEKLY_CAP))
        except (TypeError, ValueError):
            return DEFAULT_WEEKLY_CAP

    @classmethod
    async def is_enabled(cls, db: AsyncSession, store_id: int | None) -> bool:
        """门店/总部是否开启营销活动（store_settings 覆盖优先）。"""
        raw = await SystemSettingsService.effective_setting(
            db,
            "enable_marketing_campaigns",
            store_id=int(store_id) if store_id is not None else None,
            fallback="true",
        )
        return SystemSettingsService._parse_bool(raw, True)

    @classmethod
    async def assert_enabled(cls, db: AsyncSession, store_id: int | None) -> None:
        if await cls.is_enabled(db, store_id):
            return
        scope = f"门店#{int(store_id)}" if store_id is not None else "总部/全局"
        raise AppException(
            40008,
            f"{scope}已关闭「营销活动」。请到门店信息 → 业务参数 → 会员与优惠中开启 enable_marketing_campaigns",
        )

    @staticmethod
    def campaign_code_key(code: str | None) -> str:
        return (code or "").strip().upper()

    @classmethod
    async def resolve_running_campaigns(
        cls, db: AsyncSession, store_id: int, *, now=None
    ) -> list[MarketingCampaign]:
        """本店 RUNNING 活动按 code 门店覆盖总部（推荐加权等运行时解析）。"""
        from datetime import datetime

        at = now or datetime.utcnow()
        rows = list(
            (
                await db.execute(
                    select(MarketingCampaign).where(
                        MarketingCampaign.status == "RUNNING",
                        or_(
                            MarketingCampaign.store_id.is_(None),
                            MarketingCampaign.store_id == int(store_id),
                        ),
                        or_(MarketingCampaign.start_at.is_(None), MarketingCampaign.start_at <= at),
                        or_(MarketingCampaign.end_at.is_(None), MarketingCampaign.end_at >= at),
                    )
                )
            )
            .scalars()
            .all()
        )
        # 空 code 不参与同键覆盖（避免多条无 code 活动被错误压成一条）；正常创建 API 要求 code 非空
        return merge_store_over_hq(
            rows,
            key_fn=lambda c: cls.campaign_code_key(c.code) or f"__id__:{int(c.id)}",
        )

    @classmethod
    async def find_hq_same_code(
        cls, db: AsyncSession, *, code: str, exclude_id: int | None = None
    ) -> MarketingCampaign | None:
        key = cls.campaign_code_key(code)
        if not key:
            return None
        q = select(MarketingCampaign).where(
            MarketingCampaign.store_id.is_(None),
            func.upper(MarketingCampaign.code) == key,
        )
        if exclude_id is not None:
            q = q.where(MarketingCampaign.id != int(exclude_id))
        return (
            await db.execute(q.order_by(MarketingCampaign.id.asc()).limit(1))
        ).scalar_one_or_none()

    @classmethod
    async def store_has_code_override(
        cls, db: AsyncSession, *, store_id: int, code: str
    ) -> MarketingCampaign | None:
        """门店是否存在同 code 的生效/待生效活动（挡住总部发券）。"""
        key = cls.campaign_code_key(code)
        if not key:
            return None
        return (
            await db.execute(
                select(MarketingCampaign)
                .where(
                    MarketingCampaign.store_id == int(store_id),
                    func.upper(MarketingCampaign.code) == key,
                    MarketingCampaign.status.in_(("SCHEDULED", "RUNNING", "PAUSED")),
                )
                .order_by(MarketingCampaign.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @classmethod
    async def customer_home_store_id(cls, db: AsyncSession, customer_id: int) -> int | None:
        cust = await db.get(Customer, int(customer_id))
        if not cust:
            return None
        sid = getattr(cust, "registered_store_id", None) or getattr(cust, "first_seen_store_id", None)
        return int(sid) if sid else None

    @classmethod
    async def store_code_override_warning(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        code: str | None,
        exclude_id: int | None = None,
    ) -> str | None:
        """门店活动与总部同 code 时的保存/发布提示（不阻断）。"""
        if store_id is None:
            return None
        hq = await cls.find_hq_same_code(db, code=code or "", exclude_id=exclude_id)
        if not hq:
            return None
        return hq_override_warning(
            key_label="营销编码",
            key_desc=f"code={code}",
            hq_name=hq.name or str(hq.id),
        )

    @classmethod
    async def count_weekly_issues(cls, db: AsyncSession, customer_id: int) -> int:
        since = naive_local_now() - timedelta(days=7)
        cnt = await db.scalar(
            select(func.count())
            .select_from(CustomerCoupon)
            .where(
                CustomerCoupon.customer_id == int(customer_id),
                CustomerCoupon.client_request_id.like("mkt:%"),
                CustomerCoupon.obtained_at >= since,
            )
        )
        return int(cnt or 0)

    @classmethod
    def _payload(
        cls,
        row: MarketingCampaign,
        *,
        task_stats: dict | None = None,
        created_by_name: str | None = None,
    ) -> dict:
        mode = normalize_schedule_mode(getattr(row, "schedule_mode", None))
        cal = normalize_schedule_calendar(getattr(row, "schedule_calendar", None))
        data = {
            "id": int(row.id),
            "store_id": int(row.store_id) if row.store_id else None,
            "code": row.code,
            "name": row.name,
            "campaign_type": row.campaign_type,
            "status": row.status,
            "segment_id": int(row.segment_id) if row.segment_id else None,
            "coupon_template_id": int(row.coupon_template_id) if row.coupon_template_id else None,
            "schedule_mode": mode,
            "schedule_calendar": cal,
            "annual_start_month": getattr(row, "annual_start_month", None),
            "annual_start_day": getattr(row, "annual_start_day", None),
            "annual_end_month": getattr(row, "annual_end_month", None),
            "annual_end_day": getattr(row, "annual_end_day", None),
            "annual_label": format_annual_label(
                calendar=cal,
                start_month=getattr(row, "annual_start_month", None),
                start_day=getattr(row, "annual_start_day", None),
                end_month=getattr(row, "annual_end_month", None),
                end_day=getattr(row, "annual_end_day", None),
            )
            if mode == SCHEDULE_ANNUAL_MD
            else None,
            "in_schedule_window": campaign_in_schedule_window(row),
            "start_at": row.start_at.isoformat(sep=" ", timespec="seconds") if row.start_at else None,
            "end_at": row.end_at.isoformat(sep=" ", timespec="seconds") if row.end_at else None,
            "budget_cap": str(row.budget_cap) if row.budget_cap is not None else None,
            "weekly_cap_override": row.weekly_cap_override,
            "boost_product_ids": row.boost_product_ids,
            "remark": row.remark,
            "published_at": row.published_at.isoformat(sep=" ", timespec="seconds")
            if row.published_at
            else None,
            "created_by": int(row.created_by) if row.created_by else None,
            "created_by_name": created_by_name,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds")
            if row.created_at
            else None,
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds")
            if row.updated_at
            else None,
        }
        if task_stats is not None:
            data["task_stats"] = task_stats
        return data

    @classmethod
    async def _creator_names(cls, db: AsyncSession, rows: list[MarketingCampaign]) -> dict[int, str]:
        ids = {int(r.created_by) for r in rows if getattr(r, "created_by", None)}
        if not ids:
            return {}
        result = await db.execute(
            select(User.id, User.display_name, User.username).where(User.id.in_(ids))
        )
        out: dict[int, str] = {}
        for uid, display_name, username in result.all():
            label = (display_name or "").strip() or (username or "").strip() or f"#{int(uid)}"
            out[int(uid)] = label
        return out

    @classmethod
    async def payload_for(cls, db: AsyncSession, row: MarketingCampaign, **kwargs: Any) -> dict:
        names = await cls._creator_names(db, [row])
        cid = int(row.created_by) if row.created_by else None
        return cls._payload(row, created_by_name=names.get(cid) if cid else None, **kwargs)

    @classmethod
    async def get_campaign(cls, db: AsyncSession, campaign_id: int) -> MarketingCampaign:
        row = await db.get(MarketingCampaign, int(campaign_id))
        if not row:
            raise AppException(40401, "活动不存在")
        return row

    @classmethod
    async def list_campaigns(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        q = select(MarketingCampaign)
        if store_id is not None:
            q = q.where(or_(MarketingCampaign.store_id == store_id, MarketingCampaign.store_id.is_(None)))
        if status:
            q = q.where(MarketingCampaign.status == status.upper())
        total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
        rows = list(
            (
                await db.execute(
                    q.order_by(MarketingCampaign.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        names = await cls._creator_names(db, rows)
        seg_ids = {int(r.segment_id) for r in rows if r.segment_id}
        seg_codes: dict[int, str] = {}
        if seg_ids:
            for sid, scode in (
                await db.execute(
                    select(CustomerSegment.id, CustomerSegment.code).where(
                        CustomerSegment.id.in_(seg_ids)
                    )
                )
            ).all():
                seg_codes[int(sid)] = str(scode or "")
        out: list[dict] = []
        for r in rows:
            data = cls._payload(
                r,
                created_by_name=names.get(int(r.created_by)) if r.created_by else None,
            )
            if r.segment_id:
                data["segment_code"] = seg_codes.get(int(r.segment_id)) or None
            else:
                data["segment_code"] = None
            out.append(data)
        return out, total

    @classmethod
    async def upsert_campaign(
        cls,
        db: AsyncSession,
        *,
        campaign_id: int | None = None,
        store_id: int | None,
        code: str,
        name: str,
        campaign_type: str = "MEMBER_DAY",
        segment_id: int | None = None,
        coupon_template_id: int | None = None,
        schedule_mode: str | None = None,
        schedule_calendar: str | None = None,
        annual_start_month: int | None = None,
        annual_start_day: int | None = None,
        annual_end_month: int | None = None,
        annual_end_day: int | None = None,
        start_at=None,
        end_at=None,
        budget_cap=None,
        weekly_cap_override: int | None = None,
        boost_product_ids: list | None = None,
        remark: str | None = None,
        created_by: int | None = None,
    ) -> MarketingCampaign:
        code = (code or "").strip()
        name = (name or "").strip()
        if not code or not name:
            raise AppException(40001, "活动 code/name 必填")
        # 新建前先校验开关，避免未填 name 的草稿对象被 autoflush
        if campaign_id is None:
            await cls.assert_enabled(db, store_id)

        if campaign_id:
            row = await cls.get_campaign(db, campaign_id)
            if row.status not in EDITABLE_STATUSES:
                raise AppException(40001, f"仅 DRAFT 可编辑，当前 {row.status}")
            if row.store_id is None and store_id is not None:
                raise AppException(40301, "总部活动仅超管可修改")
            if store_id is not None and row.store_id not in (None, store_id):
                raise AppException(40301, "无权修改该活动")
        else:
            exists = await db.scalar(
                select(MarketingCampaign.id).where(
                    MarketingCampaign.store_id == store_id,
                    MarketingCampaign.code == code,
                )
            )
            if exists:
                raise AppException(40002, "活动 code 已存在")
            row = MarketingCampaign(
                store_id=store_id,
                code=code,
                name=name,
                status="DRAFT",
                created_by=created_by,
            )
            db.add(row)

        if segment_id:
            await SegmentService.get_segment(db, segment_id)
        if coupon_template_id:
            tmpl = await db.get(CouponTemplate, int(coupon_template_id))
            if not tmpl or int(tmpl.status) != 1:
                raise AppException(40407, "优惠券模板不存在或已停用")

        mode = normalize_schedule_mode(schedule_mode)
        cal = normalize_schedule_calendar(schedule_calendar)
        sm, sd = cls._normalize_annual_md(annual_start_month, annual_start_day)
        em, ed = cls._normalize_annual_md(annual_end_month, annual_end_day)
        if mode == SCHEDULE_ANNUAL_MD:
            if sm is None or sd is None:
                raise AppException(40001, "每年月日排期须填写起始月日")
            if (em is None) != (ed is None):
                raise AppException(40001, "结束月日须成对填写，或全部留空表示仅当天")
        else:
            sm = sd = em = ed = None
            cal = "SOLAR"

        row.code = code
        row.name = name
        row.campaign_type = (campaign_type or "MEMBER_DAY").upper()
        row.segment_id = int(segment_id) if segment_id else None
        row.coupon_template_id = int(coupon_template_id) if coupon_template_id else None
        row.schedule_mode = mode
        row.schedule_calendar = cal
        row.annual_start_month = sm
        row.annual_start_day = sd
        row.annual_end_month = em
        row.annual_end_day = ed
        row.start_at = start_at if mode == SCHEDULE_ABSOLUTE else None
        row.end_at = end_at if mode == SCHEDULE_ABSOLUTE else None
        row.budget_cap = budget_cap
        row.weekly_cap_override = weekly_cap_override
        row.boost_product_ids = boost_product_ids
        row.remark = remark
        await db.flush()
        # TimestampMixin server_default：flush 后未加载 created_at/updated_at，同步 _payload 会 MissingGreenlet
        await db.refresh(row)
        return row

    @classmethod
    async def _resolve_publish_audience(
        cls, db: AsyncSession, camp: MarketingCampaign
    ) -> tuple[Any, dict, bool]:
        seg = await SegmentService.get_segment(db, int(camp.segment_id))
        store_scoped_hq = camp.store_id is not None and seg.store_id is None
        if store_scoped_hq:
            est = await SegmentService.estimate(
                db,
                rules_json=seg.rules_json,
                store_id=int(camp.store_id),
                sample_limit=0,
            )
            mat = {
                "segment_id": int(seg.id),
                "count": int(est.get("count") or 0),
                "mode": "dynamic_store",
            }
        else:
            mat = await SegmentService.materialize(db, int(camp.segment_id))
            mat = {**mat, "mode": "snapshot"}
        return seg, mat, store_scoped_hq

    @classmethod
    async def _enqueue_issue_tasks(
        cls,
        db: AsyncSession,
        camp: MarketingCampaign,
        *,
        store_scoped_hq: bool,
        scheduled_at=None,
    ) -> tuple[int, int]:
        """按分群入队 ISSUE_COUPON；年度活动幂等键带季标识。"""
        if not camp.segment_id or not camp.coupon_template_id:
            return 0, 0
        now = naive_local_now()
        season = cls.season_for_campaign(camp)
        enqueued = 0
        skipped_dup = 0
        async for batch in SegmentService.iter_member_ids(
            db,
            int(camp.segment_id),
            prefer_snapshot=not store_scoped_hq,
            store_id=int(camp.store_id) if store_scoped_hq else None,
        ):
            for cid in batch:
                key = cls.idem_key(
                    int(camp.id),
                    cid,
                    int(camp.coupon_template_id),
                    season=season,
                )
                exists = await db.scalar(
                    select(MarketingTask.id).where(MarketingTask.idempotency_key == key)
                )
                if exists:
                    skipped_dup += 1
                    continue
                db.add(
                    MarketingTask(
                        campaign_id=int(camp.id),
                        customer_id=int(cid),
                        store_id=camp.store_id,
                        action_type="ISSUE_COUPON",
                        payload_json={
                            "template_id": int(camp.coupon_template_id),
                            "campaign_code": camp.code,
                            "season": season,
                        },
                        idempotency_key=key,
                        status="PENDING",
                        scheduled_at=scheduled_at or now,
                    )
                )
                enqueued += 1
            await db.flush()
        return enqueued, skipped_dup

    @classmethod
    async def publish(cls, db: AsyncSession, campaign_id: int, *, enqueue: bool = True) -> dict:
        """发布/启用：DRAFT/SCHEDULED → SCHEDULED|RUNNING|PAUSED，并按需入队 ISSUE_COUPON。

        年度月日活动启用后，窗口外为 PAUSED，到点由调度自动 RUNNING 并发券，无需每年重录。
        """
        camp = await cls.get_campaign(db, campaign_id)
        await cls.assert_enabled(db, camp.store_id)
        if camp.status not in PUBLISHABLE_STATUSES:
            raise AppException(40001, f"当前状态不可发布: {camp.status}")
        if not camp.segment_id:
            raise AppException(40001, "请先选择分群")
        if not camp.coupon_template_id:
            raise AppException(40001, "请先选择发券模板")

        now = naive_local_now()
        mode = normalize_schedule_mode(getattr(camp, "schedule_mode", None))
        if mode == SCHEDULE_ABSOLUTE:
            if camp.end_at and camp.end_at <= now:
                raise AppException(40001, "活动已过结束时间")
            if camp.start_at and camp.end_at and camp.start_at >= camp.end_at:
                raise AppException(40001, "开始时间必须早于结束时间")
        elif mode == SCHEDULE_ANNUAL_MD:
            if not getattr(camp, "annual_start_month", None) or not getattr(
                camp, "annual_start_day", None
            ):
                raise AppException(40001, "每年月日排期须填写起始月日")

        seg, mat, store_scoped_hq = await cls._resolve_publish_audience(db, camp)
        seg_code = (seg.code or "").strip()
        # 生日/入会周年：禁止全体；须绑含对应日判定字段的分群（如 birthday_today / member_anniversary_today）
        ctype_u = (camp.campaign_type or "").upper()
        if ctype_u == "BIRTHDAY":
            if seg_code == "all_members":
                raise AppException(
                    40001,
                    "生日活动禁止绑定「全体会员」，请改用 birthday_today（今日生日）分群",
                )
            if not SegmentService.rules_contain_field(seg.rules_json, "is_birthday_today"):
                raise AppException(
                    40001,
                    "生日活动须绑定含 is_birthday_today 的分群（预置 birthday_today）",
                )
        if ctype_u == "MEMBER_ANNIVERSARY":
            if seg_code == "all_members":
                raise AppException(
                    40001,
                    "入会周年活动禁止绑定「全体会员」，请改用 member_anniversary_today（今日入会周年）分群",
                )
            if not SegmentService.rules_contain_field(
                seg.rules_json, "is_member_anniversary_today"
            ):
                raise AppException(
                    40001,
                    "入会周年活动须绑定含 is_member_anniversary_today 的分群（预置 member_anniversary_today）",
                )
        # 全体会员须有排期：绝对短窗 或 每年月日，防止无窗口误点普发
        if seg_code == "all_members":
            if mode == SCHEDULE_ANNUAL_MD:
                pass
            elif not camp.start_at or not camp.end_at:
                raise AppException(
                    40001,
                    "全体会员活动须设置绝对起止时间，或改为「每年月日」排期，避免非节日窗口误发",
                )

        in_window = campaign_in_schedule_window(camp, now=now)
        enqueued = 0
        skipped_dup = 0
        if mode == SCHEDULE_ANNUAL_MD:
            # 启用年度排期：窗内 RUNNING 并发券；窗外 PAUSED，到点由 advance 拉起
            if in_window:
                camp.status = "RUNNING"
                if enqueue:
                    enqueued, skipped_dup = await cls._enqueue_issue_tasks(
                        db, camp, store_scoped_hq=store_scoped_hq, scheduled_at=now
                    )
            else:
                camp.status = "PAUSED"
        elif camp.start_at and camp.start_at > now:
            camp.status = "SCHEDULED"
            if enqueue:
                enqueued, skipped_dup = await cls._enqueue_issue_tasks(
                    db,
                    camp,
                    store_scoped_hq=store_scoped_hq,
                    scheduled_at=camp.start_at,
                )
        else:
            camp.status = "RUNNING"
            if enqueue:
                enqueued, skipped_dup = await cls._enqueue_issue_tasks(
                    db, camp, store_scoped_hq=store_scoped_hq, scheduled_at=now
                )

        camp.published_at = now
        # 默认 SEGMENT 规则
        has_rule = await db.scalar(
            select(MarketingRule.id).where(MarketingRule.campaign_id == camp.id).limit(1)
        )
        if not has_rule:
            db.add(
                MarketingRule(
                    campaign_id=int(camp.id),
                    rule_type="SEGMENT",
                    config_json={"segment_id": int(camp.segment_id)},
                    priority=100,
                    status=1,
                )
            )
        await db.flush()
        await db.refresh(camp)
        result: dict[str, Any] = {
            "campaign": await cls.payload_for(db, camp),
            "materialized": mat,
            "enqueued": enqueued,
            "skipped_dup": skipped_dup,
        }
        warn = await cls.store_code_override_warning(
            db, store_id=camp.store_id, code=camp.code, exclude_id=int(camp.id)
        )
        if warn:
            result["override_warning"] = warn
        return result

    @classmethod
    async def set_status(cls, db: AsyncSession, campaign_id: int, status: str) -> MarketingCampaign:
        camp = await cls.get_campaign(db, campaign_id)
        status = status.upper()
        cur = camp.status
        allowed = {
            "PAUSED": {"RUNNING"},
            "RUNNING": {"PAUSED", "SCHEDULED"},
            "ENDED": {"RUNNING", "PAUSED", "SCHEDULED"},
            "CANCELLED": {"DRAFT", "SCHEDULED", "RUNNING", "PAUSED"},
        }
        if status not in allowed or cur not in allowed[status]:
            raise AppException(40001, f"不可从 {cur} 转到 {status}")
        camp.status = status
        if status in ("ENDED", "CANCELLED", "PAUSED"):
            await db.execute(
                update(MarketingTask)
                .where(
                    MarketingTask.campaign_id == camp.id,
                    MarketingTask.status == "PENDING",
                )
                .values(status="CANCELLED" if status in ("ENDED", "CANCELLED") else "PENDING")
            )
            # PAUSED：保留 PENDING，worker 跳过；ENDED/CANCELLED：取消未发
            if status in ("ENDED", "CANCELLED"):
                pass
            elif status == "PAUSED":
                # 显式不改 PENDING，由 worker 检查活动状态
                pass
        if status == "RUNNING":
            # 恢复：无需改任务
            pass
        await db.flush()
        return camp

    @classmethod
    async def advance_campaign_lifecycle(cls, db: AsyncSession) -> dict:
        """推进排期：绝对窗 SCHEDULED→RUNNING / 过期→ENDED；
        年度月日：窗内 PAUSED→RUNNING（并按本季入队），窗外 RUNNING→PAUSED（不 ENDED）。
        """
        now = naive_local_now()
        started = 0
        ended = 0
        paused = 0
        enqueued = 0
        rows = list(
            (
                await db.execute(
                    select(MarketingCampaign).where(
                        MarketingCampaign.status.in_(["SCHEDULED", "RUNNING", "PAUSED"])
                    )
                )
            )
            .scalars()
            .all()
        )
        for c in rows:
            mode = normalize_schedule_mode(getattr(c, "schedule_mode", None))
            if mode == SCHEDULE_ANNUAL_MD:
                if c.status not in ANNUAL_ACTIVE_STATUSES:
                    continue
                if not c.published_at:
                    # 未启用过的草稿/误标状态不自动拉起
                    continue
                today = today_beijing()
                yesterday: date = today - timedelta(days=1)
                in_window = campaign_in_schedule_window(c, today=today, now=now)
                was_in = campaign_in_schedule_window(c, today=yesterday, now=now)
                entering = in_window and not was_in
                should_start = False
                if in_window and c.status == "SCHEDULED":
                    should_start = True
                elif in_window and c.status == "PAUSED":
                    if entering:
                        should_start = True
                    else:
                        # 本季尚未入队（漏跑/宕机）则补开；已入队后人工暂停不自动恢复
                        season = cls.season_for_campaign(c)
                        if season:
                            has_season = await db.scalar(
                                select(MarketingTask.id)
                                .where(
                                    MarketingTask.campaign_id == int(c.id),
                                    MarketingTask.idempotency_key.like(
                                        f"mkt:{int(c.id)}:%:{season}"
                                    ),
                                )
                                .limit(1)
                            )
                            should_start = not bool(has_season)
                if should_start:
                    c.status = "RUNNING"
                    started += 1
                    if c.segment_id and c.coupon_template_id:
                        seg = await SegmentService.get_segment(db, int(c.segment_id))
                        store_scoped_hq = c.store_id is not None and seg.store_id is None
                        n, _dup = await cls._enqueue_issue_tasks(
                            db, c, store_scoped_hq=store_scoped_hq, scheduled_at=now
                        )
                        enqueued += n
                elif (not in_window) and c.status == "RUNNING":
                    c.status = "PAUSED"
                    paused += 1
                    await db.execute(
                        update(MarketingTask)
                        .where(
                            MarketingTask.campaign_id == c.id,
                            MarketingTask.status == "PENDING",
                        )
                        .values(status="CANCELLED")
                    )
                continue

            # ABSOLUTE
            if c.status == "SCHEDULED" and c.start_at and c.start_at <= now:
                if c.end_at and c.end_at <= now:
                    c.status = "ENDED"
                    ended += 1
                else:
                    c.status = "RUNNING"
                    started += 1
                    # 生日/入会周年：刚进入 RUNNING 时立即按今日分群补圈
                    if (c.campaign_type or "").upper() in cls.DAILY_AUDIENCE_TYPES:
                        if c.segment_id and c.coupon_template_id:
                            seg = await SegmentService.get_segment(db, int(c.segment_id))
                            store_scoped_hq = c.store_id is not None and seg.store_id is None
                            n, _dup = await cls._enqueue_issue_tasks(
                                db, c, store_scoped_hq=store_scoped_hq, scheduled_at=now
                            )
                            enqueued += n
            elif c.status in ("RUNNING", "PAUSED") and c.end_at and c.end_at <= now:
                c.status = "ENDED"
                ended += 1
                await db.execute(
                    update(MarketingTask)
                    .where(
                        MarketingTask.campaign_id == c.id,
                        MarketingTask.status == "PENDING",
                    )
                    .values(status="CANCELLED")
                )
            elif (
                c.status == "RUNNING"
                and (c.campaign_type or "").upper() in cls.DAILY_AUDIENCE_TYPES
                and c.segment_id
                and c.coupon_template_id
            ):
                # 生日/入会周年：RUNNING 期间每日补圈今日命中顾客（幂等键带 Y{年}）
                seg = await SegmentService.get_segment(db, int(c.segment_id))
                store_scoped_hq = c.store_id is not None and seg.store_id is None
                n, _dup = await cls._enqueue_issue_tasks(
                    db, c, store_scoped_hq=store_scoped_hq, scheduled_at=now
                )
                enqueued += n
        await db.flush()
        return {
            "started": started,
            "ended": ended,
            "paused": paused,
            "enqueued": enqueued,
        }

    @classmethod
    async def _claim_pending_task(cls, db: AsyncSession, task_id: int) -> MarketingTask | None:
        """原子 claim：仅 PENDING → RUNNING；双 worker 时仅一行成功。"""
        now = naive_local_now()
        result = await db.execute(
            update(MarketingTask)
            .where(MarketingTask.id == int(task_id), MarketingTask.status == "PENDING")
            .values(
                status="RUNNING",
                try_count=MarketingTask.try_count + 1,
                # Core UPDATE 不触发 onupdate，须显式写，供 stale 回收判断
                updated_at=now,
            )
        )
        if not result.rowcount:
            return None
        await db.flush()
        return await db.get(MarketingTask, int(task_id))

    @classmethod
    async def reclaim_stale_running(
        cls,
        db: AsyncSession,
        *,
        stale_after: int = STALE_RUNNING_SEC,
        max_try: int = MAX_TASK_TRY,
    ) -> dict[str, int]:
        """回收崩溃残留的 RUNNING：超时未完成则复位 PENDING；超次则 FAILED。"""
        now = naive_local_now()
        cutoff = now - timedelta(seconds=max(30, int(stale_after)))
        max_try = max(1, int(max_try))
        # updated_at 为空（历史脏数据）也视为可回收
        stale_cond = or_(
            MarketingTask.updated_at.is_(None),
            MarketingTask.updated_at < cutoff,
        )
        reset = await db.execute(
            update(MarketingTask)
            .where(
                MarketingTask.status == "RUNNING",
                stale_cond,
                MarketingTask.try_count < max_try,
            )
            .values(
                status="PENDING",
                last_error="reclaimed_stale_running",
                updated_at=now,
            )
        )
        failed = await db.execute(
            update(MarketingTask)
            .where(
                MarketingTask.status == "RUNNING",
                stale_cond,
                MarketingTask.try_count >= max_try,
            )
            .values(
                status="FAILED",
                last_error="stale_running_max_try",
                executed_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        stats = {
            "requeued": int(reset.rowcount or 0),
            "failed": int(failed.rowcount or 0),
        }
        if stats["requeued"] or stats["failed"]:
            logger.warning("营销 RUNNING 残留回收: %s", stats)
        return stats

    @classmethod
    async def _consume_one_pending_task(
        cls,
        db: AsyncSession,
        task_id: int,
        stats: dict[str, int],
        *,
        now,
    ) -> None:
        """处理单条 PENDING：取消/跳过/claim+发券。claim 成功后立即 commit（RUNNING 落库）。"""
        tid = int(task_id)
        task = await db.get(MarketingTask, tid)
        if not task or task.status != "PENDING":
            return
        camp = await db.get(MarketingCampaign, int(task.campaign_id))
        if not camp or camp.status in ("ENDED", "CANCELLED"):
            cancelled = await db.execute(
                update(MarketingTask)
                .where(MarketingTask.id == tid, MarketingTask.status == "PENDING")
                .values(status="CANCELLED", executed_at=now, updated_at=now)
            )
            if cancelled.rowcount:
                stats["cancelled"] += 1
                stats["processed"] += 1
            return
        if camp.status == "PAUSED":
            return
        if camp.status == "SCHEDULED":
            return
        if camp.status != "RUNNING":
            skipped = await db.execute(
                update(MarketingTask)
                .where(MarketingTask.id == tid, MarketingTask.status == "PENDING")
                .values(
                    status="SKIPPED",
                    last_error=f"活动状态 {camp.status}",
                    executed_at=now,
                    updated_at=now,
                )
            )
            if skipped.rowcount:
                stats["skipped"] += 1
                stats["processed"] += 1
            return

        # 总部活动：顾客归属门店已有同 code 覆盖活动时跳过总部发券
        if camp.store_id is None:
            home_sid = await cls.customer_home_store_id(db, int(task.customer_id))
            if home_sid is not None:
                override = await cls.store_has_code_override(
                    db, store_id=home_sid, code=camp.code or ""
                )
                if override is not None:
                    skipped = await db.execute(
                        update(MarketingTask)
                        .where(MarketingTask.id == tid, MarketingTask.status == "PENDING")
                        .values(
                            status="SKIPPED",
                            last_error=f"门店覆盖总部同编码活动#{int(override.id)}",
                            executed_at=now,
                            updated_at=now,
                            result_json={
                                "reason": "store_override",
                                "store_id": home_sid,
                                "override_campaign_id": int(override.id),
                                "code": camp.code,
                            },
                        )
                    )
                    if skipped.rowcount:
                        stats["skipped"] += 1
                        stats["processed"] += 1
                    return

        task = await cls._claim_pending_task(db, tid)
        if task is None:
            return
        # claim 先落库：崩溃后由 reclaim_stale_running 回收，已成功条目不回滚
        await db.commit()
        task = await db.get(MarketingTask, tid)
        camp = await db.get(MarketingCampaign, int(task.campaign_id)) if task else None
        if task is None or camp is None:
            return

        try:
            if (task.action_type or "").upper() != "ISSUE_COUPON":
                task.status = "SKIPPED"
                task.last_error = f"未实现动作 {task.action_type}"
                task.executed_at = now
                task.updated_at = now
                stats["skipped"] += 1
                stats["processed"] += 1
                return

            payload = task.payload_json or {}
            template_id = int(payload.get("template_id") or camp.coupon_template_id or 0)
            if not template_id:
                raise AppException(40001, "缺少 template_id")

            cap = await cls.weekly_cap(db, camp)
            if cap > 0:
                used = await cls.count_weekly_issues(db, int(task.customer_id))
                if used >= cap:
                    task.status = "SKIPPED"
                    task.last_error = f"周频控超限 {used}/{cap}"
                    task.executed_at = now
                    task.updated_at = now
                    task.result_json = {"reason": "weekly_cap", "used": used, "cap": cap}
                    stats["skipped"] += 1
                    stats["processed"] += 1
                    return

            # 与定时沉睡唤醒/欢迎券共用模板时：已有 WELCOME 幂等券则跳过，避免双发
            if (camp.campaign_type or "").upper() in ("WAKEUP", "NEW_MEMBER"):
                welcome_key = f"WELCOME:{int(task.customer_id)}:{int(template_id)}"
                has_welcome = await db.scalar(
                    select(CustomerCoupon.id).where(
                        CustomerCoupon.client_request_id == welcome_key
                    )
                )
                if has_welcome:
                    task.status = "SKIPPED"
                    task.last_error = "已有系统定时/欢迎同模板券，跳过双发"
                    task.executed_at = now
                    task.updated_at = now
                    task.result_json = {"reason": "welcome_dup", "key": welcome_key}
                    stats["skipped"] += 1
                    stats["processed"] += 1
                    return

            issued = await CouponService.issue_by_template_id(
                db,
                customer_id=int(task.customer_id),
                template_id=template_id,
                quantity=1,
                client_request_id=task.idempotency_key,
                issued_at_store_id=int(task.store_id) if task.store_id else None,
            )
            task.status = "SUCCESS"
            task.executed_at = now
            task.updated_at = now
            task.result_json = {
                "coupon_ids": [int(c.id) for c in issued],
                "count": len(issued),
            }
            task.last_error = None
            stats["success"] += 1
        except AppException as exc:
            task.status = "FAILED"
            task.last_error = str(getattr(exc, "message", exc))[:500]
            task.executed_at = now
            task.updated_at = now
            stats["failed"] += 1
            logger.warning(
                "营销任务失败 id=%s customer=%s: %s",
                task.id,
                task.customer_id,
                task.last_error,
            )
        except Exception as exc:
            task.status = "FAILED"
            task.last_error = str(exc)[:500]
            task.executed_at = now
            task.updated_at = now
            stats["failed"] += 1
            logger.exception("营销任务异常 id=%s", task.id)
        stats["processed"] += 1

    @classmethod
    async def process_pending_tasks(cls, db: AsyncSession, *, limit: int = WORKER_BATCH) -> dict:
        """消费 PENDING ISSUE_COUPON；只调 CouponService；超频控 SKIPPED。

        单条：原子 claim → 立即 commit（RUNNING 落库）→ 发券/终态 → commit。
        长批次中途崩溃时，已成功条目保留；卡住的 RUNNING 由 stale 回收。
        """
        await cls.reclaim_stale_running(db)
        await cls.advance_campaign_lifecycle(db)
        await db.commit()

        now = naive_local_now()
        task_ids = list(
            (
                await db.execute(
                    select(MarketingTask.id)
                    .where(
                        MarketingTask.status == "PENDING",
                        or_(MarketingTask.scheduled_at.is_(None), MarketingTask.scheduled_at <= now),
                    )
                    .order_by(MarketingTask.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        stats = {"processed": 0, "success": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        for tid in task_ids:
            try:
                await cls._consume_one_pending_task(db, int(tid), stats, now=now)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("营销任务单条事务失败 id=%s", tid)
                # 若已 claim 为 RUNNING 且本条未写终态，留给 reclaim；尽量标失败避免空等
                try:
                    stuck = await db.get(MarketingTask, int(tid))
                    if stuck and stuck.status == "RUNNING":
                        stuck.status = "FAILED"
                        stuck.last_error = "worker_tx_error"
                        stuck.executed_at = naive_local_now()
                        stuck.updated_at = stuck.executed_at
                        stats["failed"] += 1
                        stats["processed"] += 1
                        await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("营销任务失败兜底写入异常 id=%s", tid)
        return stats

    @classmethod
    async def list_tasks(
        cls,
        db: AsyncSession,
        *,
        campaign_id: int,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict], int, dict]:
        await cls.get_campaign(db, campaign_id)
        q = select(MarketingTask).where(MarketingTask.campaign_id == int(campaign_id))
        if status:
            q = q.where(MarketingTask.status == status.upper())
        total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
        rows = list(
            (
                await db.execute(
                    q.order_by(MarketingTask.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        # 汇总
        agg_rows = (
            await db.execute(
                select(MarketingTask.status, func.count())
                .where(MarketingTask.campaign_id == int(campaign_id))
                .group_by(MarketingTask.status)
            )
        ).all()
        task_stats = {str(s): int(c) for s, c in agg_rows}
        items = [
            {
                "id": int(t.id),
                "campaign_id": int(t.campaign_id),
                "customer_id": int(t.customer_id),
                "store_id": int(t.store_id) if t.store_id else None,
                "action_type": t.action_type,
                "idempotency_key": t.idempotency_key,
                "status": t.status,
                "try_count": int(t.try_count or 0),
                "last_error": t.last_error,
                "result_json": t.result_json,
                "scheduled_at": t.scheduled_at.isoformat(sep=" ", timespec="seconds")
                if t.scheduled_at
                else None,
                "executed_at": t.executed_at.isoformat(sep=" ", timespec="seconds")
                if t.executed_at
                else None,
            }
            for t in rows
        ]
        return items, total, task_stats

    @classmethod
    async def report_overview(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
    ) -> dict:
        """活动 KPI：发券/任务成功、归因实收、补贴、简单 ROI。"""
        from app.models import CampaignAttribution, CustomerCoupon, MarketingCampaign, MarketingTask

        q = select(MarketingCampaign)
        if store_id is not None:
            q = q.where(or_(MarketingCampaign.store_id == store_id, MarketingCampaign.store_id.is_(None)))
        camps = list((await db.execute(q.order_by(MarketingCampaign.id.desc()).limit(50))).scalars().all())
        items = []
        for c in camps:
            task_ok = int(
                await db.scalar(
                    select(func.count())
                    .select_from(MarketingTask)
                    .where(
                        MarketingTask.campaign_id == c.id,
                        MarketingTask.status == "SUCCESS",
                    )
                )
                or 0
            )
            issued = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CustomerCoupon)
                    .where(CustomerCoupon.client_request_id.like(f"mkt:{int(c.id)}:%"))
                )
                or 0
            )
            used = int(
                await db.scalar(
                    select(func.count())
                    .select_from(CustomerCoupon)
                    .where(
                        CustomerCoupon.client_request_id.like(f"mkt:{int(c.id)}:%"),
                        CustomerCoupon.status == "USED",
                    )
                )
                or 0
            )
            gross = await db.scalar(
                select(func.coalesce(func.sum(CampaignAttribution.gross_amount), 0)).where(
                    CampaignAttribution.campaign_id == c.id,
                    CampaignAttribution.status == "ACTIVE",
                )
            )
            subsidy = await db.scalar(
                select(func.coalesce(func.sum(CampaignAttribution.subsidy_amount), 0)).where(
                    CampaignAttribution.campaign_id == c.id,
                    CampaignAttribution.status == "ACTIVE",
                )
            )
            g = float(gross or 0)
            s = float(subsidy or 0)
            roi = None if s <= 0 else round((g - s) / s, 4)
            redeem_rate = None if issued <= 0 else round(used / issued, 4)
            items.append(
                {
                    "campaign_id": int(c.id),
                    "store_id": int(c.store_id) if c.store_id else None,
                    "code": c.code,
                    "name": c.name,
                    "status": c.status,
                    "tasks_success": task_ok,
                    "coupons_issued": issued,
                    "coupons_used": used,
                    "redeem_rate": redeem_rate,
                    "attributed_gross": f"{g:.2f}",
                    "attributed_subsidy": f"{s:.2f}",
                    "roi": roi,
                }
            )
        return {"items": items, "total": len(items)}

    @classmethod
    async def retry_failed(cls, db: AsyncSession, campaign_id: int) -> dict:
        camp = await cls.get_campaign(db, campaign_id)
        if camp.status not in ("RUNNING", "PAUSED", "SCHEDULED"):
            raise AppException(40001, "当前活动状态不可重试")
        result = await db.execute(
            update(MarketingTask)
            .where(
                MarketingTask.campaign_id == int(campaign_id),
                MarketingTask.status == "FAILED",
            )
            .values(status="PENDING", last_error=None, executed_at=None)
        )
        await db.flush()
        return {"reset": int(result.rowcount or 0)}
