"""外卖/支付 Webhook Inbox：先入队应答，再异步消费。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import dumps_json, now_local
from app.models import WebhookInbox
from app.services.delivery_events import EVENT_WS_MAP
from app.services.delivery_service import DeliveryService
from app.services.outbox_worker import enqueue_outbox
from app.services.websocket_manager import ws_manager
from app.services.worker_common import backoff_seconds, claim_lease_guard, claim_token, worker_id

logger = logging.getLogger(__name__)

# 最大重试次数；超过后保持 FAILED，不再自动 claim
MAX_ATTEMPTS = 8
# 指数退避（秒）：2^attempt，上限 1 小时
BACKOFF_CAP_SEC = 3600
# PROCESSING 行超时阈值（秒）：超过即视为崩溃残留，由 reclaim 复位 PENDING
STALE_PROCESSING_SEC = 300


def _build_deferred_outbox_payload(task: dict[str, Any], channel: str) -> dict[str, Any]:
    """构建最小化、无密钥的持久化副作用 payload。"""
    action = str(task.get("action") or "").strip().lower()
    if action not in {"confirm_order", "cancel_order", "reply_urge", "print_ticket"}:
        raise ValueError(f"unsupported deferred webhook action: {action or 'empty'}")
    credentials = task.get("credentials") or {}
    store_id = task.get("store_id") or (
        credentials.get("store_id") if isinstance(credentials, dict) else None
    )
    raw = task.get("raw")
    return {
        "action": action,
        "platform": str(task.get("platform") or channel).upper(),
        "platform_order_id": str(task.get("platform_order_id") or ""),
        "store_id": int(store_id) if store_id else None,
        "order_id": int(task["order_id"]) if task.get("order_id") else None,
        "rollback_on_fail": bool(task.get("rollback_on_fail")),
        "reason": str(task.get("reason") or "")[:200],
        "ticket_type": str(task.get("ticket_type") or "KITCHEN").upper(),
        # 催单接口仅使用 reply_type；不保存整份 webhook（可能含顾客地址/手机号）。
        "raw": {"reply_type": raw.get("reply_type", 2)}
        if action == "reply_urge" and isinstance(raw, dict) else {},
    }


def _backoff_seconds(attempt_count: int) -> int:
    """指数退避：2^attempt 秒，封顶 1 小时。"""
    return backoff_seconds(attempt_count, cap=BACKOFF_CAP_SEC)


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
        payload=dumps_json(envelope),
        headers_json=dumps_json(safe_headers),
        status="PENDING",
    )
    try:
        # 必须在 SAVEPOINT 内 add + flush：begin_nested() 会先 flush 外层 pending 状态，
        # 若先 db.add(row)，唯一键冲突可能发生在 SAVEPOINT 建立前并污染调用方事务。
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row, True
    except IntegrityError:
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
    stale_filter = (
        WebhookInbox.status == "PROCESSING",
        or_(WebhookInbox.locked_at.is_(None), WebhookInbox.locked_at < cutoff),
    )
    # 若 worker 在最后一次允许尝试后崩溃，必须进入终态死信；
    # 不得重新置为 PENDING，否则会绕过 MAX_ATTEMPTS 无限重放。
    terminal_stmt = (
        update(WebhookInbox)
        .where(*stale_filter, WebhookInbox.attempt_count >= MAX_ATTEMPTS)
        .values(
            status="FAILED",
            next_retry_at=None,
            locked_at=None,
            locked_by=None,
            last_error="claim lease expired after maximum attempts",
        )
    )
    retry_stmt = (
        update(WebhookInbox)
        .where(*stale_filter, WebhookInbox.attempt_count < MAX_ATTEMPTS)
        .values(status="PENDING", locked_at=None, locked_by=None)
    )
    terminal_result = await db.execute(terminal_stmt)
    retry_result = await db.execute(retry_stmt)
    await db.commit()
    terminal_count = int(terminal_result.rowcount or 0)
    retry_count = int(retry_result.rowcount or 0)
    if retry_count:
        logger.warning("Reclaimed %s stale PROCESSING inbox events", retry_count)
    if terminal_count:
        logger.critical(
            "Webhook inbox events exhausted max_attempts after lease expiry: count=%s",
            terminal_count,
        )
    return terminal_count + retry_count


async def claim_inbox_item(db: AsyncSession, inbox_id: int, *, worker: str | None = None) -> WebhookInbox | None:
    """
    原子 claim：仅当 status=PENDING（或到点可重试的 FAILED）时 UPDATE 为 PROCESSING。
    双 worker 并发时只有一行能更新成功，另一行 rowcount=0。
    """
    wid = claim_token(worker)
    now = now_local()
    # UPDATE ... WHERE status 条件：跨 MySQL/SQLite 均可，无需 FOR UPDATE SKIP LOCKED
    stmt = (
        update(WebhookInbox)
        .where(
            WebhookInbox.id == inbox_id,
            or_(
                and_(
                    WebhookInbox.status == "PENDING",
                    WebhookInbox.attempt_count < MAX_ATTEMPTS,
                ),
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

    claim 释放使用原子 UPDATE WHERE locked_by = wid：如果处理期间行被
    reclaim_stale_inbox 复位并被另一 worker 重新 claim，原 worker 的
    UPDATE 会命中 0 行，不会覆盖新 owner 的锁（避免误释放他人锁）。
    """
    async with AsyncSessionLocal() as db:
        row = await claim_inbox_item(db, inbox_id)
        if not row:
            # 已被其他 worker claim，或 DONE / 未到重试时间
            return False
        # 每次 claim 使用唯一 token；不能用进程 worker_id 作为行锁 owner。
        wid = (row.locked_by or "")[:64]
        if not wid:
            logger.error("Webhook inbox claim 成功但缺少 owner token id=%s", inbox_id)
            return False

        try:
            envelope = json.loads(row.payload)
            raw = envelope.get("raw") if isinstance(envelope, dict) and envelope.get("_verified") else envelope
            async with claim_lease_guard(WebhookInbox, inbox_id, wid):
                result = await DeliveryService.process_raw(
                    db, row.channel, raw if isinstance(raw, dict) else {}
                )
                # 将所有必须在事务提交后执行的副作用写入同一 DB Outbox 事务。
                # 不持久化 access_token / refresh_token，也不保存整份 webhook raw。
                for task_index, task in enumerate(result.deferred_tasks or []):
                    await enqueue_outbox(
                        db,
                        aggregate_type="webhook_task",
                        aggregate_id=int(inbox_id),
                        event_kind=f"deferred_{task_index:04d}",
                        payload=_build_deferred_outbox_payload(task, row.channel),
                    )
                # 原子释放：仅当 locked_by 仍为本 worker 时才标记 DONE
                # 防止 reclaim 后新 owner 的锁被原 worker 误覆盖
                done_stmt = (
                    update(WebhookInbox)
                    .where(
                        WebhookInbox.id == inbox_id,
                        WebhookInbox.locked_by == wid[:64],
                    )
                    .values(
                        status="DONE",
                        # 业务处理已完成，不再需要保存含顾客手机号/地址的原始 webhook body。
                        # event_id、event_type、处理时间和状态保留，足以支持幂等与审计。
                        payload="{}",
                        headers_json="{}",
                        processed_at=now_local(),
                        error_message=None,
                        last_error=None,
                        next_retry_at=None,
                        locked_at=None,
                        locked_by=None,
                    )
                )
                done_result = await db.execute(done_stmt)
                from app.services.worker_common import rollback_if_claim_lost

                if await rollback_if_claim_lost(
                    db, done_result, model=WebhookInbox, row_id=inbox_id
                ):
                    return True
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
            return True
        except Exception as e:
            logger.exception("inbox 处理失败 id=%s: %s", inbox_id, e)
            async with AsyncSessionLocal() as db2:
                # 先读 attempt_count（用于计算退避），再用原子 UPDATE 释放
                row2 = await db2.get(WebhookInbox, inbox_id)
                if not row2 or row2.status == "DONE":
                    return True
                err = str(e)[:500]
                attempts = int(row2.attempt_count or 1)
                retry_at = (
                    now_local() + timedelta(seconds=_backoff_seconds(attempts))
                    if attempts < MAX_ATTEMPTS
                    else None
                )
                # 原子释放：仅当 locked_by 仍为原 worker 时才标记 FAILED
                fail_stmt = (
                    update(WebhookInbox)
                    .where(
                        WebhookInbox.id == inbox_id,
                        WebhookInbox.locked_by == wid[:64],
                    )
                    .values(
                        status="FAILED",
                        error_message=err,
                        last_error=err,
                        locked_at=None,
                        locked_by=None,
                        next_retry_at=retry_at,
                    )
                )
                fail_result = await db2.execute(fail_stmt)
                await db2.commit()
                if not fail_result.rowcount:
                    logger.warning(
                        "inbox 行在失败处理期间被 reclaim 释放 id=%s", inbox_id
                    )
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
