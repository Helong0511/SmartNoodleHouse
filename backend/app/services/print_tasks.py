"""异步打印任务（在事务提交后执行）。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.database import AsyncSessionLocal
from app.services.print_service import TICKET_CANCEL, TICKET_KITCHEN, TICKET_URGE, PrintService

logger = logging.getLogger(__name__)


async def run_print_ticket(order_id: int, ticket_type: str = TICKET_KITCHEN) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await PrintService.print_order(db, order_id, ticket_type=ticket_type)
            await db.commit()
    except Exception as e:
        logger.exception("打印任务失败 order_id=%s type=%s: %s", order_id, ticket_type, e)


def schedule_print(order_id: int, ticket_type: str = TICKET_KITCHEN) -> None:
    """请求周期内调度打印（新会话读取已提交订单）。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(run_print_ticket(int(order_id), ticket_type))
    except RuntimeError:
        logger.warning("无法调度打印任务：无运行中的事件循环 order_id=%s", order_id)


async def handle_deferred_print(task: dict[str, Any]) -> None:
    order_id = task.get("order_id")
    if not order_id:
        logger.warning("打印任务缺少 order_id: %s", task)
        return
    ticket_type = str(task.get("ticket_type") or TICKET_KITCHEN).upper()
    if ticket_type not in (TICKET_KITCHEN, TICKET_URGE, TICKET_CANCEL):
        ticket_type = TICKET_KITCHEN
    await run_print_ticket(int(order_id), ticket_type)
