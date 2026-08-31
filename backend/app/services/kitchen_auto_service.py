"""厨房 KDS 超时自动出餐：已支付且仍在厨房流水线、超时未人工处理则自动 COMPLETED。"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import naive_local_now
from app.models import Order
from app.services.order_lifecycle import apply_order_status

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
    timeout_minutes: int,
    limit: int = 50,
) -> list[Order]:
    """将超时未在 KDS 完成的堂食/小程序订单自动置为 COMPLETED。返回本次完成的订单。"""
    if timeout_minutes <= 0:
        return []

    cutoff = naive_local_now() - timedelta(minutes=timeout_minutes)
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
    for order in candidates:
        if order.status not in KITCHEN_PIPELINE_STATUSES or order.payment_status != "PAID":
            continue
        ch = (order.channel or "").strip().upper()
        if ch in EXCLUDED_CHANNELS:
            continue
        anchor_at = order_kitchen_anchor_at(order)
        if anchor_at is None or anchor_at > cutoff:
            continue
        await apply_order_status(
            db,
            order,
            "COMPLETED",
            source="KDS_TIMEOUT",
            assert_kind="SYSTEM",
            remark=f"厨房 {timeout_minutes} 分钟未处理，系统自动出餐",
        )
        completed.append(order)
    if completed:
        await db.flush()
    return completed
