"""WebSocket Redis Pub/Sub：跨进程广播"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Awaitable

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

CHANNEL = "snh:ws:broadcast"
_listener_task: asyncio.Task | None = None


async def publish_ws_event(channel: str, event: str, data: dict, store_id: int | None = None) -> None:
    payload = json.dumps(
        {"channel": channel, "event": event, "data": data, "store_id": store_id},
        default=str,
        ensure_ascii=False,
    )
    try:
        r = await get_redis()
        await r.publish(CHANNEL, payload)
    except Exception as e:
        logger.warning("WS Redis publish 失败，仅本地广播: %s", e)


async def start_ws_subscriber(on_message: Callable[[str, str, dict, int | None], Awaitable[None]]) -> None:
    """订阅 Redis，收到后回调本地 ConnectionManager.broadcast。"""
    global _listener_task

    async def _run():
        while True:
            try:
                r = await get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe(CHANNEL)
                logger.info("WS Redis subscriber started")
                async for msg in pubsub.listen():
                    if msg is None or msg.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(msg["data"])
                        await on_message(
                            payload.get("channel") or "orders",
                            payload.get("event") or "UNKNOWN",
                            payload.get("data") or {},
                            payload.get("store_id"),
                        )
                    except Exception as e:
                        logger.warning("WS subscriber 解析失败: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("WS Redis subscriber 断开，3s 后重连: %s", e)
                await asyncio.sleep(3)

    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(_run())


async def stop_ws_subscriber() -> None:
    global _listener_task
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None
