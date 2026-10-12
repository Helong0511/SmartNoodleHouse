"""营销活动：发布入队 ISSUE_COUPON、频控、任务消费。"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    CouponTemplate,
    CustomerCoupon,
    MarketingCampaign,
    MarketingRule,
    MarketingTask,
)
from app.services.coupon_service import CouponService
from app.services.segment_service import SegmentService
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

SETTING_WEEKLY_CAP = "marketing_weekly_cap"
DEFAULT_WEEKLY_CAP = 3
EDITABLE_STATUSES = frozenset({"DRAFT"})
PUBLISHABLE_STATUSES = frozenset({"DRAFT", "SCHEDULED"})
WORKER_BATCH = 50


class MarketingService:
    @staticmethod
    def idem_key(campaign_id: int, customer_id: int, template_id: int) -> str:
        return f"mkt:{int(campaign_id)}:{int(customer_id)}:{int(template_id)}"

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
    def _payload(cls, row: MarketingCampaign, *, task_stats: dict | None = None) -> dict:
        data = {
            "id": int(row.id),
            "store_id": int(row.store_id) if row.store_id else None,
            "code": row.code,
            "name": row.name,
            "campaign_type": row.campaign_type,
            "status": row.status,
            "segment_id": int(row.segment_id) if row.segment_id else None,
            "coupon_template_id": int(row.coupon_template_id) if row.coupon_template_id else None,
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
        return [cls._payload(r) for r in rows], total

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
        if campaign_id:
            row = await cls.get_campaign(db, campaign_id)
            if row.status not in EDITABLE_STATUSES:
                raise AppException(40001, f"仅 DRAFT 可编辑，当前 {row.status}")
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
            row = MarketingCampaign(store_id=store_id, code=code, status="DRAFT", created_by=created_by)
            db.add(row)

        if segment_id:
            await SegmentService.get_segment(db, segment_id)
        if coupon_template_id:
            tmpl = await db.get(CouponTemplate, int(coupon_template_id))
            if not tmpl or int(tmpl.status) != 1:
                raise AppException(40407, "优惠券模板不存在或已停用")

        row.code = code
        row.name = name
        row.campaign_type = (campaign_type or "MEMBER_DAY").upper()
        row.segment_id = int(segment_id) if segment_id else None
        row.coupon_template_id = int(coupon_template_id) if coupon_template_id else None
        row.start_at = start_at
        row.end_at = end_at
        row.budget_cap = budget_cap
        row.weekly_cap_override = weekly_cap_override
        row.boost_product_ids = boost_product_ids
        row.remark = remark
        await db.flush()
        return row

    @classmethod
    async def publish(cls, db: AsyncSession, campaign_id: int, *, enqueue: bool = True) -> dict:
        """发布：DRAFT/SCHEDULED → SCHEDULED|RUNNING，并入队 ISSUE_COUPON。"""
        camp = await cls.get_campaign(db, campaign_id)
        if camp.status not in PUBLISHABLE_STATUSES:
            raise AppException(40001, f"当前状态不可发布: {camp.status}")
        if not camp.segment_id:
            raise AppException(40001, "请先选择分群")
        if not camp.coupon_template_id:
            raise AppException(40001, "请先选择发券模板")

        now = naive_local_now()
        if camp.end_at and camp.end_at <= now:
            raise AppException(40001, "活动已过结束时间")

        # 动态分群先物化快照，保证本轮发券人群稳定
        mat = await SegmentService.materialize(db, int(camp.segment_id))
        enqueued = 0
        skipped_dup = 0
        if enqueue:
            async for batch in SegmentService.iter_member_ids(db, int(camp.segment_id), prefer_snapshot=True):
                for cid in batch:
                    key = cls.idem_key(int(camp.id), cid, int(camp.coupon_template_id))
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
                            },
                            idempotency_key=key,
                            status="PENDING",
                            scheduled_at=camp.start_at or now,
                        )
                    )
                    enqueued += 1
                await db.flush()

        if camp.start_at and camp.start_at > now:
            camp.status = "SCHEDULED"
        else:
            camp.status = "RUNNING"
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
        return {
            "campaign": cls._payload(camp),
            "materialized": mat,
            "enqueued": enqueued,
            "skipped_dup": skipped_dup,
        }

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
        """到点 SCHEDULED→RUNNING；过期 RUNNING/PAUSED→ENDED。"""
        now = naive_local_now()
        started = 0
        ended = 0
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
            if c.status == "SCHEDULED" and c.start_at and c.start_at <= now:
                if c.end_at and c.end_at <= now:
                    c.status = "ENDED"
                    ended += 1
                else:
                    c.status = "RUNNING"
                    started += 1
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
        await db.flush()
        return {"started": started, "ended": ended}

    @classmethod
    async def process_pending_tasks(cls, db: AsyncSession, *, limit: int = WORKER_BATCH) -> dict:
        """消费 PENDING ISSUE_COUPON；只调 CouponService；超频控 SKIPPED。"""
        await cls.advance_campaign_lifecycle(db)
        now = naive_local_now()
        tasks = list(
            (
                await db.execute(
                    select(MarketingTask)
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
        for task in tasks:
            camp = await db.get(MarketingCampaign, int(task.campaign_id))
            if not camp or camp.status in ("ENDED", "CANCELLED"):
                task.status = "CANCELLED"
                task.executed_at = now
                stats["cancelled"] += 1
                stats["processed"] += 1
                continue
            if camp.status == "PAUSED":
                # 暂停期间不消费，留给恢复后
                continue
            if camp.status == "SCHEDULED":
                continue
            if camp.status != "RUNNING":
                task.status = "SKIPPED"
                task.last_error = f"活动状态 {camp.status}"
                task.executed_at = now
                stats["skipped"] += 1
                stats["processed"] += 1
                continue

            task.status = "RUNNING"
            task.try_count = int(task.try_count or 0) + 1
            await db.flush()

            try:
                if (task.action_type or "").upper() != "ISSUE_COUPON":
                    task.status = "SKIPPED"
                    task.last_error = f"未实现动作 {task.action_type}"
                    task.executed_at = now
                    stats["skipped"] += 1
                    stats["processed"] += 1
                    continue

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
                        task.result_json = {"reason": "weekly_cap", "used": used, "cap": cap}
                        stats["skipped"] += 1
                        stats["processed"] += 1
                        continue

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
                stats["failed"] += 1
                logger.exception("营销任务异常 id=%s", task.id)
            stats["processed"] += 1
        await db.flush()
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
