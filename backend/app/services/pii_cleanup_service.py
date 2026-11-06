"""PII 生命周期清理：对外卖订单中已完结的收件人/骑手 PII 按保留期脱敏。

保留期内（默认 30 天）保持明文以便售后/客服；超期后字段置为脱敏标记，
不影响订单财务/状态数据，仅清除个人可识别信息。
"""
from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import DeliveryCallbackLog, DeliveryOrder, DeliveryRefund, Order, WebhookInbox

logger = logging.getLogger(__name__)

# 默认保留期（天）：超期后脱敏 PII
DEFAULT_PII_RETENTION_DAYS = 30
# 终态集合：仅这些状态的订单才参与 PII 清理
_FINAL_STATUSES = ("COMPLETED", "CANCELLED", "REFUNDED")


async def anonymize_expired_delivery_pii(
    db: AsyncSession, *, retention_days: int = DEFAULT_PII_RETENTION_DAYS
) -> int:
    """将超期已完结外卖订单的 recipient/rider PII 字段置 NULL。

    条件：
    1. 关联订单状态 ∈ {COMPLETED, CANCELLED, REFUNDED}
    2. 订单 completed_at 或 cancelled_at < now - retention_days
    3. delivery_orders 行的 PII 尚未脱敏（recipient_phone IS NOT NULL）
    """
    from datetime import timedelta

    from sqlalchemy import or_, select

    cutoff = now_local() - timedelta(days=retention_days)
    # 子查询：获取已完结且超过保留期的订单 ID
    expired_order_ids = select(Order.id).where(
        Order.status.in_(_FINAL_STATUSES),
        or_(
            (Order.completed_at.isnot(None) & (Order.completed_at < cutoff)),
            (Order.cancelled_at.isnot(None) & (Order.cancelled_at < cutoff)),
        ),
    )
    # 主更新：脱敏这些订单对应的 delivery_orders PII
    stmt = (
        update(DeliveryOrder)
        .where(
            DeliveryOrder.order_id.in_(expired_order_ids),
            or_(
                DeliveryOrder.recipient_name.isnot(None),
                DeliveryOrder.recipient_phone.isnot(None),
                DeliveryOrder.recipient_address.isnot(None),
                DeliveryOrder.rider_name.isnot(None),
                DeliveryOrder.rider_phone.isnot(None),
                DeliveryOrder.raw_payload.isnot(None),
            ),
        )
        .values(
            recipient_name=None,
            recipient_phone=None,
            recipient_address=None,
            rider_name=None,
            rider_phone=None,
            raw_payload=None,
        )
    )
    result = await db.execute(stmt)
    # 原始回调/退款 payload 也可能包含收件人、骑手和地址信息，必须与订单 PII 同期脱敏。
    await db.execute(
        update(DeliveryRefund)
        .where(DeliveryRefund.order_id.in_(expired_order_ids), DeliveryRefund.raw_payload.isnot(None))
        .values(raw_payload=None)
    )
    await db.execute(
        update(DeliveryCallbackLog)
        .where(DeliveryCallbackLog.order_id.in_(expired_order_ids), DeliveryCallbackLog.payload.isnot(None))
        .values(payload=None)
    )
    # DONE 回调在消费成功时已清空；达到最大重试次数且 next_retry_at 为空的
    # FAILED Inbox 属于终态死信，不会再自动消费。超过保留期后清理其中的顾客 PII，
    # 但保留 event_id/channel/status/attempt_count/时间戳供审计与排障。
    await db.execute(
        update(WebhookInbox)
        .where(
            WebhookInbox.status == "FAILED",
            WebhookInbox.next_retry_at.is_(None),
            WebhookInbox.received_at < cutoff,
            WebhookInbox.payload != "{}",
        )
        .values(
            payload="{}",
            headers_json="{}",
            error_message=None,
            last_error=None,
        )
    )
    await db.commit()
    count = int(result.rowcount or 0)
    if count:
        logger.info("Anonymized PII for %s completed delivery orders (older than %s days)", count, retention_days)
    return count
