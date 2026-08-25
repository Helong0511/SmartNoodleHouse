"""外卖平台异步回传任务（回调先应答，再调平台 API）"""
from __future__ import annotations

import logging
from typing import Any

from app.services.delivery_context import PlatformCredentials
from app.services.delivery_providers import get_delivery_provider

logger = logging.getLogger(__name__)


async def run_deferred_tasks(tasks: list[dict[str, Any]]) -> None:
    """仅调用平台 OpenAPI，不另开 DB 会话（避免与测试内存库/生产库不一致）"""
    if not tasks:
        return
    for task in tasks:
        action = task.get("action")
        platform = task.get("platform", "").upper()
        platform_order_id = task.get("platform_order_id", "")
        creds_data = task.get("credentials") or {}
        creds = PlatformCredentials(**creds_data) if creds_data else None
        provider = get_delivery_provider(platform, creds)
        try:
            if action == "confirm_order":
                await provider.confirm_order(platform_order_id)
            elif action == "reply_urge":
                await provider.reply_urge(platform_order_id, task.get("raw") or {})
            else:
                logger.warning("未知 deferred task: %s", action)
        except Exception as e:
            logger.exception("deferred task 失败 %s %s: %s", action, platform_order_id, e)
