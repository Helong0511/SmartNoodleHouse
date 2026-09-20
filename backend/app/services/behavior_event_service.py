"""顾客隐式行为事件采集：白名单校验、event_id 幂等去重、个性化开关丢弃。

Task 4 仅负责可靠落库；画像增量学习在 taste_learning_service（Task 5）按事件权重消费。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.models import CustomerBehaviorEvent

logger = logging.getLogger(__name__)

# 埋点事件白名单（学习权重见 taste_learning_service.EVENT_WEIGHTS）
EVENT_TYPES: frozenset[str] = frozenset({
    "EXPOSE",        # 推荐位曝光
    "CLICK",         # 点击商品/推荐卡片
    "CART_ADD",      # 加入购物车
    "CART_REMOVE",   # 移出购物车（负反馈）
    "ORDER_PAID",    # 支付成功（服务端支付链路也会补记）
    "REORDER",       # 一键再来一单/复购
    "FEEDBACK",      # 订单后轻量评价（明细走 product_feedbacks，此事件作画像触发）
})

MAX_BATCH = 50
MAX_PAYLOAD_CHARS = 2048
MAX_SCENE_LEN = 20
MAX_BUCKET_LEN = 80
MAX_EVENT_ID_LEN = 64


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    return iv if iv > 0 else None


def normalize_event(raw: Any) -> tuple[CustomerBehaviorEvent | None, str | None]:
    """校验并构造模型实例（未持久化）。返回 (event, drop_reason)。"""
    if not isinstance(raw, dict):
        return None, "not_object"
    etype = str(raw.get("event_type") or "").strip().upper()
    if etype not in EVENT_TYPES:
        return None, "bad_type"
    eid = str(raw.get("event_id") or "").strip()
    if not eid:
        # 客户端兜底缺失时服务端生成（仅保证不崩，幂等仍以客户端 event_id 为准）
        eid = uuid.uuid4().hex
    eid = eid[:MAX_EVENT_ID_LEN]

    payload = raw.get("payload")
    if payload is not None:
        if not isinstance(payload, dict):
            payload = None
        else:
            try:
                if len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_PAYLOAD_CHARS:
                    payload = None
            except (TypeError, ValueError):
                payload = None

    event = CustomerBehaviorEvent(
        event_id=eid,
        event_type=etype,
        scene=(str(raw.get("scene") or "").strip()[:MAX_SCENE_LEN] or None),
        product_id=_safe_int(raw.get("product_id")),
        category_id=_safe_int(raw.get("category_id")),
        store_id=_safe_int(raw.get("store_id")),
        ab_bucket=(str(raw.get("ab_bucket") or "").strip()[:MAX_BUCKET_LEN] or None),
        payload=payload,
    )
    return event, None


async def ingest_events(db, customer, raw_events: list[Any]) -> dict[str, Any]:
    """批量入库。返回 {accepted, duplicate, dropped, reason?}。"""
    total = len(raw_events)
    # 隐私：顾客关闭个性化推荐 → 不落任何行为数据
    if not int(getattr(customer, "recommend_opt_in", 1) or 0):
        return {"accepted": 0, "duplicate": 0, "dropped": total, "reason": "opt_out"}

    normalized: list[CustomerBehaviorEvent] = []
    dropped = 0
    batch_seen: set[str] = set()
    for raw in raw_events:
        event, reason = normalize_event(raw)
        if event is None:
            dropped += 1
            continue
        if event.event_id in batch_seen:
            dropped += 1
            continue
        batch_seen.add(event.event_id)
        event.customer_id = int(customer.id)
        normalized.append(event)

    if not normalized:
        return {"accepted": 0, "duplicate": 0, "dropped": dropped}

    existing_rows = await db.execute(
        select(CustomerBehaviorEvent.event_id).where(
            CustomerBehaviorEvent.customer_id == int(customer.id),
            CustomerBehaviorEvent.event_id.in_(batch_seen),
        )
    )
    existing = {r[0] for r in existing_rows.all()}
    to_insert = [e for e in normalized if e.event_id not in existing]
    duplicate = len(normalized) - len(to_insert)

    if to_insert:
        db.add_all(to_insert)
        await db.flush()
    return {"accepted": len(to_insert), "duplicate": duplicate, "dropped": dropped}
