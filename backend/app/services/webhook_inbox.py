"""外卖/支付 Webhook Inbox：先入队应答，再异步消费。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
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

# 最大重试次数；超过后保持 FAILED，不再自动 claim
MAX_ATTEMPTS = 8
# 指数退避基数（秒）：2^attempt，上限 3600
BACKOFF_BASE_SEC = 2
BACKOFF_CAP_SEC = 3600
# PROCESSING 行超时阈值（秒）：超过即视为崩溃残留，由 reclaim 复位 PENDING
STALE_PROCESSING_SEC = 300


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _backoff_seconds(attempt_count: int) -> int:
    """指数退避：2^attempt 秒，封顶 1 小时。"""
    exp = max(0, int(attempt_count))
    return min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC**exp)


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
    # P1-44: 脱敏敏感 header，防止 Authorization/Cookie/token 泄露到 inbox 存储
    _SENSITIVE_HEADERS = frozenset({
        "authorization", "cookie", "set-cookie", "x-api-key",
        "x-auth-token", "x-access-token", "proxy-authorization",
    })
    safe_headers = {
        k: ("[REDACTED]" if k.lower() in _SENSITIVE_HEADERS else str(v))
        for k, v in headers.items()
    }
    # 存已验签后的结构化 raw，消费时不再二次验签
    envelope = {"_verified": True, "raw": raw}
    row = WebhookInbox(
        channel=channel,
        event_id=event_id,
        event_type=event_type,
        payload=json.dumps(envelope, ensure_ascii=False, default=str),
        headers_json=json.dumps(safe_headers, ensure_ascii=False),
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


async def reclaim_stale_inbox(
    db: AsyncSession, *, stale_after: int = STALE_PROCESSING_SEC
) -> int:
    """回收崩溃残留的 PROCESSING inbox 行：locked_at < now - stale_after → 复位 PENDING。

    复位时不清零 attempt_count（避免无限重试），仅释放锁、保留退避。
    """
    cutoff = now_local() - timedelta(seconds=stale_after)
    stmt = (
        update(WebhookInbox)
        .where(
            WebhookInbox.status == "PROCESSING",
            or_(WebhookInbox.locked_at.is_(None), WebhookInbox.locked_at < cutoff),
        )
        .values(status="PENDING", locked_at=None, locked_by=None)
    )
    result = await db.execute(stmt)
    await db.commit()
    count = int(result.rowcount or 0)
    if count:
        logger.warning("Reclaimed %s stale PROCESSING inbox events", count)
    return count


async def claim_inbox_item(db: AsyncSession, inbox_id: int, *, worker: str | None = None) -> WebhookInbox | None:
    """
    原子 claim：仅当 status=PENDING（或到点可重试的 FAILED）时 UPDATE 为 PROCESSING。
    双 worker 并发时只有一行能更新成功，另一行 rowcount=0。
    """
    wid = worker or _worker_id()
    now = now_local()
    # UPDATE ... WHERE status 条件：跨 MySQL/SQLite 均可，无需 FOR UPDATE SKIP LOCKED
    stmt = (
        update(WebhookInbox)
        .where(
            WebhookInbox.id == inbox_id,
            or_(
                WebhookInbox.status == "PENDING",
                and_(
                    WebhookInbox.status == "FAILED",
                    WebhookInbox.attempt_count < MAX_ATTEMPTS,
                    or_(
                        WebhookInbox.next_retry_at.is_(None),
                        WebhookInbox.next_retry_at <= now,
                    ),
                ),
            ),
        )
        .values(
            status="PROCESSING",
            locked_at=now,
            locked_by=wid[:64],
            attempt_count=WebhookInbox.attempt_count + 1,
        )
    )
    result = await db.execute(stmt)
    await db.commit()
    if not result.rowcount:
        return None
    return await db.get(WebhookInbox, inbox_id)


async def process_inbox_item(inbox_id: int) -> bool:
    """Claim + 处理单条 inbox。

    返回 True 表示成功 claim（无论最终 DONE 还是 FAILED）；
    返回 False 表示未被 claim（已被其他 worker 占用 / DONE / 未到重试时间）。
    """
    async with AsyncSessionLocal() as db:
        row = await claim_inbox_item(db, inbox_id)
        if not row:
            # 已被其他 worker claim，或 DONE / 未到重试时间
            return False

        try:
            envelope = json.loads(row.payload)
            raw = envelope.get("raw") if isinstance(envelope, dict) and envelope.get("_verified") else envelope
            result = await DeliveryService.process_raw(db, row.channel, raw if isinstance(raw, dict) else {})
            row.status = "DONE"
            row.processed_at = now_local()
            row.error_message = None
            row.last_error = None
            row.next_retry_at = None
            row.locked_at = None
            row.locked_by = None
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
            return True
        except Exception as e:
            logger.exception("inbox 处理失败 id=%s: %s", inbox_id, e)
            async with AsyncSessionLocal() as db2:
                row2 = await db2.get(WebhookInbox, inbox_id)
                if not row2 or row2.status == "DONE":
                    return True
                err = str(e)[:500]
                row2.status = "FAILED"
                row2.error_message = err
                row2.last_error = err
                row2.locked_at = None
                row2.locked_by = None
                attempts = int(row2.attempt_count or 1)
                if attempts < MAX_ATTEMPTS:
                    row2.next_retry_at = now_local() + timedelta(seconds=_backoff_seconds(attempts))
                else:
                    row2.next_retry_at = None
                await db2.commit()
            return True


async def process_pending_inbox(limit: int = 20) -> int:
    """扫描可消费条目并逐条 claim；已被占用的 id 在 claim 时跳过。"""
    now = now_local()
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(WebhookInbox.id)
                .where(
                    or_(
                        WebhookInbox.status == "PENDING",
                        and_(
                            WebhookInbox.status == "FAILED",
                            WebhookInbox.attempt_count < MAX_ATTEMPTS,
                            or_(
                                WebhookInbox.next_retry_at.is_(None),
                                WebhookInbox.next_retry_at <= now,
                            ),
                        ),
                    )
                )
                .order_by(WebhookInbox.received_at)
                .limit(limit)
            )
        ).scalars().all()
        ids = list(rows)
    claimed = 0
    for iid in ids:
        # process_inbox_item 内部原子 claim；仅当成功 claim（DONE 或 FAILED）时计 +1
        if await process_inbox_item(iid):
            claimed += 1
    return claimed
