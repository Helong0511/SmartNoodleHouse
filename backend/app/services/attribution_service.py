"""营销活动归因：券优先 → 48h 内推送 → 公益；退款冲减。"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import money, naive_local_now
from app.models import (
    CampaignAttribution,
    CouponTemplate,
    CustomerCoupon,
    MarketingMessageLog,
    Order,
)
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

SETTING_WINDOW = "attribution_window_hours"
DEFAULT_WINDOW = 48
_MKT_RE = re.compile(r"^mkt:(\d+):")


class AttributionService:
    @classmethod
    async def window_hours(cls, db: AsyncSession) -> int:
        raw = await SystemSettingsService.effective_setting(
            db, SETTING_WINDOW, fallback=DEFAULT_WINDOW
        )
        try:
            return max(1, min(int(str(raw).strip() or DEFAULT_WINDOW), 720))
        except (TypeError, ValueError):
            return DEFAULT_WINDOW

    @staticmethod
    def campaign_id_from_coupon_req(req: str | None) -> int | None:
        if not req:
            return None
        m = _MKT_RE.match(str(req).strip())
        if not m:
            return None
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return None

    @classmethod
    async def attribute_paid_order(cls, db: AsyncSession, order_id: int) -> CampaignAttribution | None:
        order = await db.get(Order, int(order_id))
        if not order or not order.customer_id:
            return None
        if (order.payment_status or "").upper() not in ("PAID", "PARTIAL_REFUND"):
            return None

        # 已有 ACTIVE 归因则幂等返回
        existing = await db.scalar(
            select(CampaignAttribution).where(
                CampaignAttribution.order_id == int(order.id),
                CampaignAttribution.status == "ACTIVE",
            )
        )
        if existing:
            return existing

        campaign_id: int | None = None
        attr_type = "COUPON"
        coupon_id: int | None = None
        subsidy = Decimal("0.00")

        for cid in (getattr(order, "customer_coupon_id", None), getattr(order, "customer_voucher_id", None)):
            if not cid:
                continue
            cc = await db.get(CustomerCoupon, int(cid))
            if not cc:
                continue
            cid_mkt = cls.campaign_id_from_coupon_req(cc.client_request_id)
            if cid_mkt:
                campaign_id = cid_mkt
                coupon_id = int(cc.id)
                discount = getattr(order, "coupon_discount", None) or getattr(order, "voucher_discount", None)
                if discount:
                    subsidy = money(discount)
                else:
                    tmpl = await db.get(CouponTemplate, int(cc.template_id)) if cc.template_id else None
                    if tmpl and (tmpl.coupon_type or "").upper() == "FIXED":
                        subsidy = money(tmpl.discount_value)
                break

        if campaign_id is None:
            # 48h 内成功推送
            hours = await cls.window_hours(db)
            since = naive_local_now() - timedelta(hours=hours)
            msg = (
                await db.execute(
                    select(MarketingMessageLog)
                    .where(
                        MarketingMessageLog.customer_id == int(order.customer_id),
                        MarketingMessageLog.status == "SUCCESS",
                        MarketingMessageLog.campaign_id.isnot(None),
                        MarketingMessageLog.sent_at >= since,
                    )
                    .order_by(MarketingMessageLog.sent_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if msg and msg.campaign_id:
                campaign_id = int(msg.campaign_id)
                attr_type = "MESSAGE"

        if campaign_id is None:
            return None

        gross = money(getattr(order, "total_amount", None) or 0)
        net = money(gross - subsidy)
        key = f"attr:order:{int(order.id)}"
        dup = await db.scalar(
            select(CampaignAttribution).where(CampaignAttribution.idempotency_key == key)
        )
        if dup:
            return dup
        row = CampaignAttribution(
            campaign_id=int(campaign_id),
            customer_id=int(order.customer_id),
            store_id=int(order.store_id) if order.store_id else None,
            order_id=int(order.id),
            attribution_type=attr_type,
            coupon_id=coupon_id,
            gross_amount=gross,
            subsidy_amount=subsidy,
            net_amount=net,
            status="ACTIVE",
            idempotency_key=key,
            attributed_at=naive_local_now(),
            meta={"window_hours": await cls.window_hours(db)},
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def reverse_for_order(cls, db: AsyncSession, order_id: int) -> int:
        rows = list(
            (
                await db.execute(
                    select(CampaignAttribution).where(
                        CampaignAttribution.order_id == int(order_id),
                        CampaignAttribution.status == "ACTIVE",
                    )
                )
            )
            .scalars()
            .all()
        )
        now = naive_local_now()
        for r in rows:
            r.status = "REVERSED"
            r.reversed_at = now
        await db.flush()
        return len(rows)
