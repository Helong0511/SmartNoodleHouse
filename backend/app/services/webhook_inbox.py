"""外卖/支付 Webhook Inbox：先入队应答，再异步消费。"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import now_local
from app.models import WebhookInbox
from app.services.delivery_events import EVENT_WS_MAP
from app.services.delivery_service import DeliveryService
from app.services.delivery_tasks import run_deferred_tasks
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)


def make_event_id(platform: str, raw: dict[str, Any], body: bytes) -> str:
    """平台事件唯一 ID：优先官方字段，否则 body hash。"""
    for key in ("requestId", "request_id", "msg_id", "msgid", "notify_id", "event_id"):
        if raw.get(key):
            return f"{platform}:{raw[key]}"
    order_id = ""
    if raw.get("order_id"):
        order_id = str(raw["order_id"])
    elif raw.get("wm_order_id_view"):
        order_id = str(raw["wm_order_id_view"])
    elif isinstance(raw.get("order"), dict) and raw["order"].get("id"):
        order_id = str(raw["order"]["id"])
    elif raw.get("orderId"):
        order_id = str(raw["orderId"])
    event = str(raw.get("type") or raw.get("event") or raw.get("status") or "")
    digest = hashlib.sha256(body).hexdigest()[:24]
    return f"{platform}:{order_id}:{event}:{digest}"


async def enqueue_delivery_webhook(
    db: AsyncSession,
    *,
    platform: str,
    headers: dict,
    body: bytes,
    raw: dict[str, Any],
    event_type: str | None = None,
) -> tuple[WebhookInbox, bool]:
    """
    写入 inbox。返回 (row, is_new)。
    重复 event_id 时返回已有行且 is_new=False。
    """
    channel = platform.upper()
    event_id = make_event_id(channel, raw, body)
    # 存已验签后的结构化 raw，消费时不再二次验签
    envelope = {"_verified": True, "raw": raw}
    row = WebhookInbox(
        channel=channel,
        event_id=event_id,
        event_type=event_type,
        payload=json.dumps(envelope, ensure_ascii=False, default=str),
        headers_json=json.dumps({k: str(v) for k, v in headers.items()}, ensure_ascii=False),
        status="PENDING",
    )
    db.add(row)
    try:
        await db.flush()
        return row, True
    except IntegrityError:
        await db.rollback()
        existing = (
            await db.execute(
                select(WebhookInbox).where(
                    WebhookInbox.channel == channel,
                    WebhookInbox.event_id == event_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing, False
        raise


async def process_inbox_item(inbox_id: int) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(WebhookInbox, inbox_id)
        if not row or row.status == "DONE":
            return
        row.status = "PROCESSING"
        await db.commit()

        try:
            envelope = json.loads(row.payload)
            raw = envelope.get("raw") if isinstance(envelope, dict) and envelope.get("_verified") else envelope
            result = await DeliveryService.process_raw(db, row.channel, raw if isinstance(raw, dict) else {})
            row.status = "DONE"
            row.processed_at = now_local()
            row.error_message = None
            await db.commit()

            ws_event = EVENT_WS_MAP.get(result.event, "DELIVERY_UNKNOWN")
            payload = {"channel": row.channel, "event": result.event, **result.extra}
            if result.order:
                payload.update(
                    {
                        "order_id": result.order.id,
                        "order_no": result.order.order_no,
                        "status": result.order.status,
                        "store_id": result.order.store_id,
                    }
                )
            await ws_manager.notify_order_event(ws_event, payload)
            if result.deferred_tasks:
                await run_deferred_tasks(result.deferred_tasks)
        except Exception as e:
            logger.exception("inbox 处理失败 id=%s: %s", inbox_id, e)
            async with AsyncSessionLocal() as db2:
                row2 = await db2.get(WebhookInbox, inbox_id)
                if row2:
                    row2.status = "FAILED"
                    row2.error_message = str(e)[:500]
                    await db2.commit()


async def process_pending_inbox(limit: int = 20) -> int:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(WebhookInbox)
                .where(WebhookInbox.status == "PENDING")
                .order_by(WebhookInbox.received_at)
                .limit(limit)
            )
        ).scalars().all()
        ids = [r.id for r in rows]
    for iid in ids:
        await process_inbox_item(iid)
    return len(ids)
