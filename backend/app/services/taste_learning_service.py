"""顾客口味画像增量学习：行为事件/支付/评价 → 三维偏好分（标签/分类/单品）。

机制：
- 权重：EXPOSE 0.2 / CLICK 1 / CART_ADD 2×qty / CART_REMOVE -1.5 /
  ORDER_PAID 3×qty / REORDER +1(=4) / 评价 POSITIVE +2、NEGATIVE -3。
- 时间衰减：30 天半衰期，新事件 fold 前对该行 raw_weight 乘 2^(-Δ天/30)。
- 归一化：score = logistic(raw / SCALE)，落在 0..1，负反馈可把分压到 0.5 以下。
- 增量水位：顾客全部画像行的 MAX(last_event_at)；只 fold 更晚的事件，天然幂等。
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models import (
    CustomerBehaviorEvent,
    CustomerTasteProfile,
    Order,
    OrderItem,
    Product,
    ProductCategory,
)
from app.services.taste_profile_service import DIMENSION_META, get_or_derive

logger = logging.getLogger(__name__)

HALF_LIFE_DAYS = 30.0
RAW_SCALE = 4.0
EVENT_BATCH_LIMIT = 1000

# 事件基础权重（ORDER_PAID/CART_ADD 再乘数量）
EVENT_WEIGHTS: dict[str, float] = {
    "EXPOSE": 0.2,
    "CLICK": 1.0,
    "CART_ADD": 2.0,
    "CART_REMOVE": -1.5,
    "ORDER_PAID": 3.0,
    "REORDER": 4.0,
    "FEEDBACK": 0.0,  # 评价走 product_feedbacks 专门通道，避免双计
}

FEEDBACK_WEIGHTS = {"POSITIVE": 2.0, "NEGATIVE": -3.0, "NEUTRAL": 0.0}


def _logistic(raw: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, raw / RAW_SCALE))))


def _decay_factor(previous_at: datetime | None, now_at: datetime) -> float:
    if previous_at is None:
        return 1.0
    delta = (now_at - previous_at).total_seconds()
    if delta <= 0:
        return 1.0
    return 2.0 ** (-(delta / 86400.0) / HALF_LIFE_DAYS)


def _quantize(v: float) -> float:
    return round(max(0.0, min(1.0, v)), 4)


class TasteLearningService:
    """全部为无状态类方法；db 会话由调用方管理（API/ outbox / scheduler 统一提交）。"""

    # ---------- 信号键展开 ----------

    @staticmethod
    async def _product_signals(
        db,
        product_ids: set[int],
    ) -> tuple[dict[int, Product], dict[int, str], dict[int, list[tuple[str, str]]]]:
        """返回 products / category_names / keys-per-product（TASTE_TAG 键列表）。"""
        products: dict[int, Product] = {}
        if product_ids:
            for p in (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars().all():
                products[int(p.id)] = p
        cat_ids = {int(p.category_id) for p in products.values() if p.category_id}
        cat_names: dict[int, str] = {}
        if cat_ids:
            for c in (
                await db.execute(select(ProductCategory).where(ProductCategory.id.in_(cat_ids)))
            ).scalars().all():
                cat_names[int(c.id)] = c.name

        keys_by_product: dict[int, list[tuple[str, str]]] = {}
        for pid in product_ids:
            p = products.get(pid)
            if not p:
                keys_by_product[pid] = []
                continue
            tag_keys: list[tuple[str, str]] = []
            try:
                profile = await get_or_derive(db, pid)
            except Exception:
                logger.exception("学习时读取产品画像失败 product_id=%s", pid)
                profile = None
            if profile:
                for tag in profile.tags or []:
                    tag_keys.append((f"tag:{tag}", tag))
                for k, v in (profile.dimensions or {}).items():
                    try:
                        iv = int(v)
                    except (TypeError, ValueError):
                        continue
                    if iv > 0 and k in DIMENSION_META:
                        label = DIMENSION_META[k][0]
                        tag_keys.append((f"dim:{k}={iv}", f"{label}{iv}级"))
            keys_by_product[pid] = tag_keys
        return products, cat_names, keys_by_product

    @staticmethod
    def _event_keys(
        event: CustomerBehaviorEvent,
        products: dict[int, Product],
        cat_names: dict[int, str],
        keys_by_product: dict[int, list[tuple[str, str]]],
    ) -> list[tuple[str, str, str]]:
        """展开为 (dim_type, target_key, target_name) 列表。"""
        keys: list[tuple[str, str, str]] = []
        pid = int(event.product_id) if event.product_id else None
        product = products.get(pid) if pid else None
        cat_id = int(event.category_id) if event.category_id else (
            int(product.category_id) if product and product.category_id else None
        )
        if cat_id is not None:
            keys.append(("CATEGORY", str(cat_id), cat_names.get(cat_id, f"分类{cat_id}")))
        if pid is not None and product is not None:
            keys.append(("PRODUCT", str(pid), product.name))
            for tkey, tname in keys_by_product.get(pid, []):
                keys.append(("TASTE_TAG", tkey, tname))
        return keys

    @staticmethod
    def _event_weight(event: CustomerBehaviorEvent) -> float:
        base = EVENT_WEIGHTS.get(event.event_type, 0.0)
        if not base:
            return 0.0
        qty = 1
        if event.event_type in ("ORDER_PAID", "CART_ADD") and isinstance(event.payload, dict):
            try:
                qty = max(1, int(event.payload.get("qty") or 1))
            except (TypeError, ValueError):
                qty = 1
        return base * qty

    # ---------- 画像行折叠 ----------

    @staticmethod
    async def _load_rows(db, customer_id: int) -> dict[tuple[str, str], CustomerTasteProfile]:
        result = await db.execute(
            select(CustomerTasteProfile).where(CustomerTasteProfile.customer_id == customer_id)
        )
        return {(r.dim_type, r.target_key): r for r in result.scalars().all()}

    @staticmethod
    def _fold_row(
        row: CustomerTasteProfile | None,
        customer_id: int,
        dim_type: str,
        target_key: str,
        target_name: str,
        weight: float,
        at: datetime,
    ) -> CustomerTasteProfile:
        if row is None:
            row = CustomerTasteProfile(
                customer_id=customer_id,
                dim_type=dim_type,
                target_key=target_key,
                target_name=target_name,
                raw_weight=0,
                score=0.5,
                event_count=0,
            )
        raw = float(row.raw_weight or 0) * _decay_factor(row.last_event_at, at) + weight
        row.raw_weight = round(raw, 4)
        row.score = _quantize(_logistic(raw))
        row.target_name = target_name or row.target_name
        row.event_count = int(row.event_count or 0) + 1
        row.last_event_at = at
        row.last_computed_at = at
        return row

    # ---------- 主入口 ----------

    @staticmethod
    async def learn_from_events(db, customer_id: int, *, limit: int = EVENT_BATCH_LIMIT) -> int:
        """增量 fold 水位之后的事件；返回处理事件数。"""
        rows = await TasteLearningService._load_rows(db, customer_id)
        watermark = max((r.last_event_at for r in rows.values() if r.last_event_at), default=None)
        q = select(CustomerBehaviorEvent).where(
            CustomerBehaviorEvent.customer_id == customer_id
        ).order_by(CustomerBehaviorEvent.created_at, CustomerBehaviorEvent.id)
        if watermark is not None:
            q = q.where(CustomerBehaviorEvent.created_at > watermark)
        events = list((await db.execute(q.limit(limit))).scalars().all())
        if not events:
            return 0

        pids = {int(e.product_id) for e in events if e.product_id}
        products, cat_names, keys_by_product = await TasteLearningService._product_signals(db, pids)

        for ev in events:
            w = TasteLearningService._event_weight(ev)
            if not w:
                continue
            at = ev.created_at
            for dim_type, tkey, tname in TasteLearningService._event_keys(
                ev, products, cat_names, keys_by_product
            ):
                idx = (dim_type, tkey)
                rows[idx] = TasteLearningService._fold_row(
                    rows.get(idx), customer_id, dim_type, tkey, tname, w, at
                )
        db.add_all([r for r in rows.values()])
        await db.flush()
        return len(events)

    @staticmethod
    async def learn_from_feedback(db, feedback) -> None:
        """轻量评价直接 fold（评价明细已独立入库；此处分流口味信号）。"""
        sentiment = (feedback.sentiment or "NEUTRAL").upper()
        w = FEEDBACK_WEIGHTS.get(sentiment, 0.0)
        if not w or not feedback.product_id:
            return
        products, cat_names, keys_by_product = await TasteLearningService._product_signals(
            db, {int(feedback.product_id)}
        )
        rows = await TasteLearningService._load_rows(db, int(feedback.customer_id))
        fake_ev = CustomerBehaviorEvent(
            customer_id=feedback.customer_id,
            product_id=feedback.product_id,
            event_type="FEEDBACK",
        )
        at = feedback.created_at
        for dim_type, tkey, tname in TasteLearningService._event_keys(
            fake_ev, products, cat_names, keys_by_product
        ):
            idx = (dim_type, tkey)
            rows[idx] = TasteLearningService._fold_row(
                rows.get(idx), int(feedback.customer_id), dim_type, tkey, tname, w, at
            )
        db.add_all(list(rows.values()))
        await db.flush()

    @staticmethod
    async def record_full_refund_events(db, order_id: int) -> int:
        """整单退款（payment_status=REFUNDED）：逐品记 CART_REMOVE 合成事件并学习。

        以事件表 event_id 幂等 + 水位增量 fold，outbox 重试安全；部分退款不在此处理
        （既有 PreferenceService.PARTIAL_REFUND_WEIGHT 已覆盖规格偏好降权）。
        """
        order = await db.get(Order, order_id)
        if not order or not order.customer_id or (order.payment_status or "") != "REFUNDED":
            return 0
        items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order_id)
        )).scalars().all())
        pids = [int(i.product_id) for i in items if i.product_id]
        if not pids:
            return 0
        existing = {
            r[0]
            for r in (
                await db.execute(
                    select(CustomerBehaviorEvent.event_id).where(
                        CustomerBehaviorEvent.customer_id == int(order.customer_id),
                        CustomerBehaviorEvent.event_id.in_(
                            [f"order_refund:{order_id}:{pid}" for pid in pids]
                        ),
                    )
                )
            ).all()
        }
        new_events = [
            CustomerBehaviorEvent(
                event_id=f"order_refund:{order_id}:{pid}",
                customer_id=int(order.customer_id),
                store_id=int(order.store_id) if order.store_id else None,
                event_type="CART_REMOVE",
                scene="refund",
                product_id=pid,
                payload={"order_id": order_id, "refund": True},
            )
            for pid in pids
            if f"order_refund:{order_id}:{pid}" not in existing
        ]
        if new_events:
            db.add_all(new_events)
            await db.flush()
        return await TasteLearningService.learn_from_events(db, int(order.customer_id))

    # ---------- 支付 outbox 挂接 ----------

    @staticmethod
    async def record_paid_order_events(db, order_id: int) -> int:
        """为已支付订单逐明细补记 ORDER_PAID 事件（event_id 幂等），并即时增量学习。

        由 paid_side_effects 的 pref_update handler 调用；outbox 重试安全：
        重复事件被唯一约束去重，学习以水位增量 fold。
        """
        from app.models import Customer

        order = await db.get(Order, order_id)
        if not order or not order.customer_id:
            return 0
        customer = await db.get(Customer, int(order.customer_id))
        if customer and not int(getattr(customer, "recommend_opt_in", 1) or 0):
            return 0
        items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order_id)
        )).scalars().all())
        qty_by_product: dict[int, int] = {}
        cat_by_product: dict[int, int] = {}
        for it in items:
            if not it.product_id:
                continue
            pid = int(it.product_id)
            qty_by_product[pid] = qty_by_product.get(pid, 0) + int(it.quantity or 1)
            cat_by_product[pid] = 0  # 类别由学习时产品反查，事件 category_id 可空

        existing = {
            r[0]
            for r in (
                await db.execute(
                    select(CustomerBehaviorEvent.event_id).where(
                        CustomerBehaviorEvent.customer_id == int(order.customer_id),
                        CustomerBehaviorEvent.event_id.in_(
                            [f"order_paid:{order_id}:{pid}" for pid in qty_by_product]
                        ),
                    )
                )
            ).all()
        }
        new_events = [
            CustomerBehaviorEvent(
                event_id=f"order_paid:{order_id}:{pid}",
                customer_id=int(order.customer_id),
                store_id=int(order.store_id) if order.store_id else None,
                event_type="ORDER_PAID",
                scene="order",
                product_id=pid,
                payload={"order_id": order_id, "qty": qty},
            )
            for pid, qty in qty_by_product.items()
            if f"order_paid:{order_id}:{pid}" not in existing
        ]
        if new_events:
            db.add_all(new_events)
            await db.flush()
        return await TasteLearningService.learn_from_events(db, int(order.customer_id))

    # ---------- scheduler 兜底 ----------

    @staticmethod
    async def learn_recent_customers(db, *, since: datetime, limit: int = 200) -> dict[str, int]:
        """扫描 since 之后有新事件的顾客并增量学习（5 分钟更新目标的兜底通道）。"""
        customer_ids = [
            int(r[0])
            for r in (
                await db.execute(
                    select(CustomerBehaviorEvent.customer_id)
                    .where(CustomerBehaviorEvent.created_at >= since)
                    .distinct()
                    .limit(limit)
                )
            ).all()
        ]
        learned = 0
        events = 0
        for cid in customer_ids:
            try:
                n = await TasteLearningService.learn_from_events(db, cid)
                if n:
                    learned += 1
                    events += n
            except Exception:
                logger.exception("兜底口味学习失败 customer_id=%s", cid)
        return {"customers": learned, "events": events}
