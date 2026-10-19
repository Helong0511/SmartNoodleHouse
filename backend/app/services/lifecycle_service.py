"""顾客生命周期 / RFM 夜批与只读查询。

阈值来自 system_settings：lifecycle_thresholds_json / rfm_thresholds_json。
不引入 tenant_id；按 customers.last_order_at + 近 90 天订单聚合。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import money, naive_local_now, serialize_dt
from app.models import Customer, CustomerLifecycle, Order
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

_PAID = ("PAID", "PARTIAL_REFUND")

_DEFAULT_LC = {
    "new_days": 7,
    "active_days": 30,
    "at_risk_days": 90,
    "dormant_days": 120,
}

# RFM：沉默天 / 90 天单量 / 90 天实付 的 1–5 分界（含端点按升序）
_DEFAULT_RFM = {
    "r_days": [7, 30, 60, 90],  # ≤7→5 … >90→1
    "f_orders": [1, 2, 4, 8],  # ≥8→5 …
    "m_amount": [30, 80, 150, 300],
}


def _parse_json_setting(raw: str | None, fallback: dict) -> dict:
    if not raw or not str(raw).strip():
        return dict(fallback)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            out = dict(fallback)
            out.update(data)
            return out
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return dict(fallback)


def _score_asc_thresholds(value: float, cuts: list[float], *, higher_better: bool) -> int:
    """cuts 升序 4 个分界 → 5 档。higher_better=True 时值越大分越高。"""
    if len(cuts) < 4:
        cuts = list(cuts) + [cuts[-1]] * (4 - len(cuts)) if cuts else [0, 0, 0, 0]
    c = [float(x) for x in cuts[:4]]
    if higher_better:
        if value >= c[3]:
            return 5
        if value >= c[2]:
            return 4
        if value >= c[1]:
            return 3
        if value >= c[0]:
            return 2
        return 1
    # 值越小越好（如沉默天）
    if value <= c[0]:
        return 5
    if value <= c[1]:
        return 4
    if value <= c[2]:
        return 3
    if value <= c[3]:
        return 2
    return 1


def compute_lifecycle_code(
    customer: Customer,
    *,
    thresholds: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    """与服务卡口径一致，阈值可配置。"""
    th = thresholds or _DEFAULT_LC
    now = now or naive_local_now()
    if customer.member_status != "MEMBER":
        return "GUEST"
    new_days = int(th.get("new_days", 7))
    active_days = int(th.get("active_days", 30))
    at_risk_days = int(th.get("at_risk_days", 90))
    dormant_days = int(th.get("dormant_days", 120))
    activated = customer.member_activated_at
    if activated and (now - activated).days <= new_days:
        return "NEW"
    lo = customer.last_order_at
    if lo is None:
        return "DORMANT"
    days = (now - lo).days
    if days <= active_days:
        return "ACTIVE"
    if days <= at_risk_days:
        return "AT_RISK"
    if days <= dormant_days:
        return "DORMANT"
    return "LOST"


def compute_rfm_segment(r: int, f: int, m: int) -> str:
    """粗粒度 RFM 标签，供店长筛选。"""
    avg = (r + f + m) / 3.0
    if r >= 4 and f >= 4 and m >= 4:
        return "CHAMPIONS"
    if r >= 4 and f >= 3:
        return "LOYAL"
    if r >= 4 and f <= 2:
        return "NEW_PROMISING"
    if r <= 2 and f >= 3:
        return "AT_RISK"
    if r <= 2 and f <= 2:
        return "HIBERNATING"
    if avg >= 3.5:
        return "POTENTIAL"
    return "OTHER"


class LifecycleService:
    @staticmethod
    async def load_thresholds(db: AsyncSession) -> tuple[dict, dict]:
        lc_raw = await SystemSettingsService.get_value(db, "lifecycle_thresholds_json", "")
        rfm_raw = await SystemSettingsService.get_value(db, "rfm_thresholds_json", "")
        return _parse_json_setting(lc_raw, _DEFAULT_LC), _parse_json_setting(rfm_raw, _DEFAULT_RFM)

    @classmethod
    def compute_row(
        cls,
        customer: Customer,
        *,
        order_count_90d: int,
        pay_amount_90d: Decimal,
        lc_th: dict,
        rfm_th: dict,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or naive_local_now()
        code = compute_lifecycle_code(customer, thresholds=lc_th, now=now)
        if customer.last_order_at is None:
            silence = 9999
        else:
            silence = max(0, (now - customer.last_order_at).days)
        r_cuts = [float(x) for x in (rfm_th.get("r_days") or _DEFAULT_RFM["r_days"])]
        f_cuts = [float(x) for x in (rfm_th.get("f_orders") or _DEFAULT_RFM["f_orders"])]
        m_cuts = [float(x) for x in (rfm_th.get("m_amount") or _DEFAULT_RFM["m_amount"])]
        r_score = _score_asc_thresholds(float(silence), r_cuts, higher_better=False)
        f_score = _score_asc_thresholds(float(order_count_90d), f_cuts, higher_better=True)
        m_score = _score_asc_thresholds(float(pay_amount_90d), m_cuts, higher_better=True)
        return {
            "lifecycle_code": code,
            "rfm_segment": compute_rfm_segment(r_score, f_score, m_score),
            "r_score": r_score,
            "f_score": f_score,
            "m_score": m_score,
            "silence_days": silence,
            "order_count_90d": int(order_count_90d),
            "pay_amount_90d": money(pay_amount_90d),
            "computed_at": now,
        }

    @classmethod
    async def get_payload(cls, db: AsyncSession, customer_id: int) -> dict | None:
        row = (
            await db.execute(
                select(CustomerLifecycle).where(CustomerLifecycle.customer_id == int(customer_id))
            )
        ).scalar_one_or_none()
        if not row:
            customer = await db.get(Customer, int(customer_id))
            if not customer:
                return None
            lc_th, rfm_th = await cls.load_thresholds(db)
            snap = cls.compute_row(
                customer,
                order_count_90d=0,
                pay_amount_90d=Decimal("0"),
                lc_th=lc_th,
                rfm_th=rfm_th,
            )
            return {
                "customer_id": int(customer_id),
                "lifecycle_code": snap["lifecycle_code"],
                "rfm_segment": snap["rfm_segment"],
                "r_score": snap["r_score"],
                "f_score": snap["f_score"],
                "m_score": snap["m_score"],
                "silence_days": snap["silence_days"],
                "order_count_90d": 0,
                "pay_amount_90d": "0.00",
                "computed_at": None,
                "source": "live",
            }
        return {
            "customer_id": int(row.customer_id),
            "lifecycle_code": row.lifecycle_code,
            "rfm_segment": row.rfm_segment,
            "r_score": row.r_score,
            "f_score": row.f_score,
            "m_score": row.m_score,
            "silence_days": int(row.silence_days or 0),
            "order_count_90d": int(row.order_count_90d or 0),
            "pay_amount_90d": str(row.pay_amount_90d or "0.00"),
            "computed_at": serialize_dt(row.computed_at),
            "source": "snapshot",
        }

    @classmethod
    async def refresh_all(
        cls,
        db: AsyncSession,
        *,
        batch_size: int = 500,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """夜批：全量重算 lifecycle 快照。"""
        now = now or naive_local_now()
        lc_th, rfm_th = await cls.load_thresholds(db)
        since = now - timedelta(days=90)
        updated = 0
        last_id = 0
        while True:
            customers = list(
                (
                    await db.execute(
                        select(Customer)
                        .where(Customer.id > last_id, Customer.status == 1)
                        .order_by(Customer.id.asc())
                        .limit(batch_size)
                    )
                ).scalars().all()
            )
            if not customers:
                break
            ids = [int(c.id) for c in customers]
            last_id = ids[-1]
            agg_rows = (
                await db.execute(
                    select(
                        Order.customer_id,
                        func.count(Order.id),
                        func.coalesce(func.sum(Order.total_amount), 0),
                    )
                    .where(
                        Order.customer_id.in_(ids),
                        Order.payment_status.in_(_PAID),
                        Order.paid_at >= since,
                    )
                    .group_by(Order.customer_id)
                )
            ).all()
            agg = {
                int(cid): (int(cnt), money(Decimal(str(amt or 0))))
                for cid, cnt, amt in agg_rows
            }
            existing = {
                int(r.customer_id): r
                for r in (
                    await db.execute(
                        select(CustomerLifecycle).where(CustomerLifecycle.customer_id.in_(ids))
                    )
                ).scalars().all()
            }
            for c in customers:
                cnt, amt = agg.get(int(c.id), (0, Decimal("0.00")))
                snap = cls.compute_row(
                    c,
                    order_count_90d=cnt,
                    pay_amount_90d=amt,
                    lc_th=lc_th,
                    rfm_th=rfm_th,
                    now=now,
                )
                row = existing.get(int(c.id))
                if row is None:
                    row = CustomerLifecycle(customer_id=int(c.id))
                    db.add(row)
                row.lifecycle_code = snap["lifecycle_code"]
                row.rfm_segment = snap["rfm_segment"]
                row.r_score = snap["r_score"]
                row.f_score = snap["f_score"]
                row.m_score = snap["m_score"]
                row.silence_days = snap["silence_days"]
                row.order_count_90d = snap["order_count_90d"]
                row.pay_amount_90d = snap["pay_amount_90d"]
                row.computed_at = snap["computed_at"]
                updated += 1
            # 按批 commit：夜批中途崩溃时已算完的顾客快照保留
            await db.commit()
        return {"updated": updated, "at": now.isoformat(sep=" ", timespec="seconds")}
