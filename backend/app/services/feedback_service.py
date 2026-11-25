"""轻量评价服务：已付订单单品标签评价，驱动口味画像正/负反馈学习。

设计要点：
- 标签白名单（受控枚举），sentiment 由标签集合服务端推导，不信任前端传值；
- 每订单每单品仅一条（uq_pf_order_product_customer），重复提交保持幂等（保留首次）；
- 画像学习只对 recommend_opt_in=1 的顾客生效，关闭个性化后评价仅作业务数据留存。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, ProductFeedback

logger = logging.getLogger(__name__)

# 正向标签
POSITIVE_VERDICTS = {
    "TASTY",          # 好吃/对味
    "WILL_REORDER",   # 想再点
    "FRESH",          # 新鲜
    "GOOD_PORTION",   # 分量足
}
# 负向标签
NEGATIVE_VERDICTS = {
    "TOO_SPICY",      # 太辣
    "TOO_SWEET",      # 太甜
    "TOO_SOUR",       # 太酸
    "TOO_SALTY",      # 太咸
    "TOO_BITTER",     # 太苦
    "TOO_BLAND",      # 太淡/没味
    "TOO_SMALL",      # 分量少
    "TOO_EXPENSIVE",  # 不值
    "NOT_FRESH",      # 不新鲜
    "DISLIKE",        # 不喜欢
}
NEUTRAL_VERDICTS = {"PORTION_OK"}
VERDICT_WHITELIST = POSITIVE_VERDICTS | NEGATIVE_VERDICTS | NEUTRAL_VERDICTS
MAX_VERDICTS_PER_ITEM = 5
MAX_REMARK_LEN = 200

# 允许评价的支付状态（部分退款订单仍可评价）
EVALUABLE_PAYMENT_STATUSES = {"PAID", "PARTIAL_REFUND"}


def derive_sentiment(verdicts: list[str]) -> str:
    pos = sum(1 for v in verdicts if v in POSITIVE_VERDICTS)
    neg = sum(1 for v in verdicts if v in NEGATIVE_VERDICTS)
    if pos > neg:
        return "POSITIVE"
    if neg > pos:
        return "NEGATIVE"
    return "NEUTRAL"


class FeedbackService:
    @staticmethod
    async def list_order_feedback(db: AsyncSession, order_id: int) -> list[ProductFeedback]:
        return list((await db.execute(
            select(ProductFeedback)
            .where(ProductFeedback.order_id == order_id)
            .order_by(ProductFeedback.id)
        )).scalars().all())

    @staticmethod
    async def order_feedback_payload(db: AsyncSession, order: Order) -> dict:
        """回显：订单可评单品 + 已评内容，供前端评价条渲染/置灰。"""
        from app.models import OrderItem

        items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )).scalars().all())
        given = {f.product_id: f for f in await FeedbackService.list_order_feedback(db, order.id)}
        return {
            "can_evaluate": (order.payment_status or "") in EVALUABLE_PAYMENT_STATUSES,
            "items": [
                {
                    "product_id": int(it.product_id),
                    "product_name": it.product_name_snapshot,
                    "feedback": (
                        {
                            "verdicts": given[int(it.product_id)].verdicts or [],
                            "sentiment": given[int(it.product_id)].sentiment,
                            "remark": given[int(it.product_id)].remark,
                        }
                        if int(it.product_id) in given
                        else None
                    ),
                }
                for it in items
            ],
        }

    @staticmethod
    async def submit_order_feedback(
        db: AsyncSession,
        order: Order,
        items: list[dict],
    ) -> dict:
        """批量提交订单单品评价。

        - product_id 必须属于订单明细，否则 ValueError；
        - verdicts 必须在白名单，否则 ValueError；
        - 已评单品按幂等跳过（保留首次评价，画像不重复学习）；
        - 新评价落库后同步驱动画像 fold（失败由调用方隔离）。
        """
        from app.models import OrderItem
        from app.services.taste_learning_service import TasteLearningService

        # Serialize feedback persistence with both merge and opt-out cleanup.
        # The caller's Order may be stale: refresh its owner only after acquiring
        # Gate → Order → Customer, and abort if ownership changed while waiting.
        if not order.customer_id:
            raise ValueError("订单未关联顾客，无法提交评价")
        order, locked_customer = await TasteLearningService._lock_order_customer_for_learning(
            db, int(order.id)
        )
        if order is None or locked_customer is None:
            raise ValueError("顾客或订单不存在，无法提交评价")

        order_items = list((await db.execute(
            select(OrderItem).where(OrderItem.order_id == order.id)
        )).scalars().all())
        valid_pids = {int(it.product_id) for it in order_items if it.product_id}
        existing = {
            f.product_id: f
            for f in (
                await db.execute(
                    select(ProductFeedback)
                    .where(ProductFeedback.order_id == order.id)
                    .order_by(ProductFeedback.id)
                    .with_for_update()
                )
            ).scalars().all()
        }

        saved: list[int] = []
        skipped: list[int] = []
        for raw in items:
            pid = int(raw["product_id"])
            if pid not in valid_pids:
                raise ValueError(f"餐品 {pid} 不属于该订单")
            if pid in existing:
                skipped.append(pid)
                continue
            verdicts = [str(v).strip().upper() for v in (raw.get("verdicts") or []) if str(v).strip()]
            if not verdicts:
                raise ValueError("评价标签不能为空")
            illegal = [v for v in verdicts if v not in VERDICT_WHITELIST]
            if illegal:
                raise ValueError(f"非法评价标签: {','.join(illegal)}")
            # 去重保序 + 数量上限
            uniq: list[str] = []
            for v in verdicts:
                if v not in uniq:
                    uniq.append(v)
            verdicts = uniq[:MAX_VERDICTS_PER_ITEM]
            remark = (raw.get("remark") or None)
            if remark:
                remark = remark.strip()[:MAX_REMARK_LEN] or None

            fb = ProductFeedback(
                order_id=order.id,
                product_id=pid,
                customer_id=int(order.customer_id),
                store_id=int(order.store_id) if order.store_id else None,
                verdicts=verdicts,
                sentiment=derive_sentiment(verdicts),
                remark=remark,
            )
            db.add(fb)
            await db.flush()
            saved.append(pid)
            # 关闭个性化的顾客：评价仅留存，不更新推荐画像
            # 画像学习用 savepoint 隔离，失败不回滚已落库的评价
            if int(getattr(locked_customer, "recommend_opt_in", 1) or 0) == 1:
                try:
                    async with db.begin_nested():
                        await TasteLearningService.learn_from_feedback(
                            db, fb, locked_customer=locked_customer
                        )
                except Exception:
                    logger.exception(
                        "评价画像学习失败，写入 Outbox 补偿 order_id=%s product_id=%s customer_id=%s",
                        order.id,
                        pid,
                        order.customer_id,
                    )
                    # 评价已经 flush；补偿事件必须与评价处于同一外层事务。
                    # 若 Outbox 入队失败则向调用方传播，避免提交一条永远不会补学的评价。
                    from app.services.outbox_worker import enqueue_outbox

                    await enqueue_outbox(
                        db,
                        aggregate_type="feedback_learning",
                        aggregate_id=int(fb.id),
                        event_kind="taste_learning",
                        payload={"feedback_id": int(fb.id)},
                    )

        return {"saved": saved, "skipped_existing": skipped}


async def _handle_feedback_taste_learning(db: AsyncSession, row) -> None:
    """按当前订单归属与授权状态补学评价；画像 fold 与 Outbox DONE 同事务提交。"""
    from app.models import ProductFeedback
    from app.services.taste_learning_service import TasteLearningService

    feedback_snapshot = (
        await db.execute(
            select(ProductFeedback).where(
                ProductFeedback.id == int(row.aggregate_id)
            )
        )
    ).scalar_one_or_none()
    if feedback_snapshot is None:
        return

    # 初始评价读取只用于取得 order_id；权威顾客归属必须在 Gate → Order → Customer 后刷新。
    order, customer = await TasteLearningService._lock_order_customer_for_learning(
        db, int(feedback_snapshot.order_id)
    )
    if order is None or customer is None:
        return

    feedback = (
        await db.execute(
            select(ProductFeedback)
            .where(ProductFeedback.id == int(row.aggregate_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if feedback is None:
        return
    if (
        feedback.order_id != order.id
        or feedback.customer_id != order.customer_id
    ):
        raise RuntimeError(
            "feedback ownership changed while acquiring recommendation gate; retry outbox"
        )
    if not int(getattr(customer, "recommend_opt_in", 0) or 0):
        return

    await TasteLearningService.learn_from_feedback(
        db, feedback, locked_customer=customer
    )


# Worker imports this module after restart when it claims feedback-learning events.
from app.services.outbox_worker import register_outbox_handler

register_outbox_handler("feedback_learning", "taste_learning")(
    _handle_feedback_taste_learning
