"""厨房 KDS 超时自动出餐：已支付且仍在厨房流水线、超时未人工处理则自动 COMPLETED。"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.response import naive_local_now
from app.models import Order
from app.services.order_lifecycle import apply_order_status
from app.services.system_settings_service import SystemSettingsService

logger = logging.getLogger(__name__)

# 与厨房 KDS 活跃列表一致
KITCHEN_PIPELINE_STATUSES = ("PAID", "CONFIRMED", "PREPARING", "COOKING", "READY")
# 外卖平台订单由平台回调推进，不参与本自动出餐
EXCLUDED_CHANNELS = frozenset({"MEITUAN", "ELEME"})


def order_kitchen_anchor_at(order: Order):
    """计时时点：支付时间优先，其次下单/创建时间。"""
    return order.paid_at or order.ordered_at or order.created_at


async def auto_complete_stale_kitchen_orders(
    db: AsyncSession,
    *,
    timeout_minutes: int | None = None,
    limit: int = 50,
) -> list[Order]:
    """将超时未在 KDS 完成的堂食/小程序订单自动置为 COMPLETED。按门店 effective_setting 判定超时。"""
    settings = get_settings()
    global_timeout = (
        int(timeout_minutes)
        if timeout_minutes is not None
        else await SystemSettingsService.effective_int(
            db, "kitchen_auto_complete_minutes", settings.kitchen_auto_complete_minutes
        )
    )
    overrides_raw = await SystemSettingsService.store_override_map(db, "kitchen_auto_complete_minutes")
    store_timeouts: dict[int, int] = {}
    for sid, val in overrides_raw.items():
        try:
            store_timeouts[int(sid)] = int(val)
        except (TypeError, ValueError):
            continue

    # 取正超时中的最小值做粗筛；全 0 则关闭
    positive = [t for t in [global_timeout, *store_timeouts.values()] if t > 0]
    if not positive:
        return []
    min_timeout = min(positive)
    cutoff = naive_local_now() - timedelta(minutes=min_timeout)
    anchor = func.coalesce(Order.paid_at, Order.ordered_at, Order.created_at)
    result = await db.execute(
        select(Order)
        .where(
            Order.status.in_(KITCHEN_PIPELINE_STATUSES),
            Order.payment_status == "PAID",
            or_(Order.channel.is_(None), Order.channel.notin_(tuple(EXCLUDED_CHANNELS))),
            anchor <= cutoff,
        )
        .order_by(anchor.asc(), Order.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    candidates = list(result.scalars().all())
    completed: list[Order] = []
    now = naive_local_now()
    for order in candidates:
        if order.status not in KITCHEN_PIPELINE_STATUSES or order.payment_status != "PAID":
            continue
        ch = (order.channel or "").strip().upper()
        if ch in EXCLUDED_CHANNELS:
            continue
        minutes = store_timeouts.get(int(order.store_id), global_timeout)
        if minutes <= 0:
            continue
        anchor_at = order_kitchen_anchor_at(order)
        if anchor_at is None or anchor_at > now - timedelta(minutes=minutes):
            continue
        await apply_order_status(
            db,
            order,
            "COMPLETED",
            source="KDS_TIMEOUT",
            assert_kind="SYSTEM",
            remark=f"厨房 {minutes} 分钟未处理，系统自动出餐",
        )
        completed.append(order)
    if completed:
        await db.flush()
    return completed
