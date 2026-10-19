"""外卖平台异步回传任务（回调先应答，再调平台 API）"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import DeliveryOrder, Order
from app.services.delivery_context import PlatformCredentials
from app.services.delivery_providers import get_delivery_provider
from app.services.print_tasks import handle_deferred_print

logger = logging.getLogger(__name__)


async def run_deferred_tasks(tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        return
    for task in tasks:
        action = task.get("action")
        if action == "print_ticket":
            try:
                await handle_deferred_print(task)
            except Exception as e:
                logger.exception("deferred print 失败 order_id=%s: %s", task.get("order_id"), e)
            continue
        platform = task.get("platform", "").upper()
        platform_order_id = task.get("platform_order_id", "")
        creds_data = task.get("credentials") or {}
        creds = PlatformCredentials(**creds_data) if creds_data else None
        provider = get_delivery_provider(platform, creds)
        try:
            if action == "confirm_order":
                ok = await provider.confirm_order(platform_order_id)
                if ok is False and task.get("rollback_on_fail"):
                    await _rollback_confirm(task.get("order_id"), platform, platform_order_id)
            elif action == "reply_urge":
                await provider.reply_urge(platform_order_id, task.get("raw") or {})
            elif action == "cancel_order":
                # 库存不足拒单等：平台取消在 DB 事务外执行
                reason = task.get("reason") or "商家取消"
                await provider.cancel_order(platform_order_id, reason)
            else:
                logger.warning("未知 deferred task: %s", action)
        except Exception as e:
            logger.exception("deferred task 失败 %s %s: %s", action, platform_order_id, e)
            if action == "confirm_order" and task.get("rollback_on_fail"):
                await _rollback_confirm(task.get("order_id"), platform, platform_order_id)


async def _rollback_confirm(order_id: int | None, platform: str, platform_order_id: str) -> None:
    async with AsyncSessionLocal() as db:
        d_order = None
        if order_id:
            d_order = (
                await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order_id))
            ).scalar_one_or_none()
        if not d_order:
            d_order = (
                await db.execute(
                    select(DeliveryOrder).where(
                        DeliveryOrder.platform == platform.upper(),
                        DeliveryOrder.platform_order_id == platform_order_id,
                    )
                )
            ).scalar_one_or_none()
        if not d_order:
            return
        d_order.delivery_status = "PENDING"
        d_order.last_sync_at = now_local()
        order = await db.get(Order, d_order.order_id)
        if order and order.status == "CONFIRMED":
            from app.services.order_lifecycle import apply_order_status

            await apply_order_status(
                db,
                order,
                "PAID",
                source="DELIVERY",
                assert_kind="SYSTEM",
                remark="平台接单失败回滚",
            )
        await db.commit()
        logger.error("平台接单失败，已回滚本地状态 order_id=%s platform=%s", d_order.order_id, platform)
