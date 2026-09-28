"""支付成功后副作用（积分/券核销/口味/里程碑发券）：DB 事务型 outbox，不依赖 Redis。

支付 PAID 同事务写入 outbox 事件：
  - points_award       → CustomerService.award_points_for_paid_order
  - coupon_mark_used   → CouponService.mark_used
  - pref_update        → PreferenceService.update_from_order
  - milestone_coupon   → CouponService.issue_milestones_after_paid

各副作用函数自身按 order_id / 幂等键去重，故 outbox 重试不会重复入账。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.outbox_worker import enqueue_outbox, register_outbox_handler

logger = logging.getLogger(__name__)


async def enqueue_paid_side_effects(db: AsyncSession, order) -> None:
    """与支付 PAID 同事务写入 outbox 事件。commit 由调用方负责。

    幂等：(aggregate_type, aggregate_id, event_kind) UNIQUE 保证不重复入队。
    """
    if not order or not getattr(order, "id", None):
        return
    order_id = int(order.id)
    # 独立事件：失败粒度更细，单条 retry 不影响其它
    for kind in ("points_award", "coupon_mark_used", "pref_update", "milestone_coupon"):
        try:
            await enqueue_outbox(
                db,
                aggregate_type="paid_side_effect",
                aggregate_id=order_id,
                event_kind=kind,
                payload={"order_id": order_id},
            )
        except Exception:
            # enqueue_outbox 在 UNIQUE 冲突时返回 is_new=False 不抛；此处异常多为 DB 故障
            logger.exception("写入 paid_side_effect outbox 失败 order_id=%s kind=%s", order_id, kind)
            raise


# ---------- outbox handlers：由 outbox_worker.process_pending_outbox 调用 ----------


@register_outbox_handler("paid_side_effect", "points_award")
async def _handle_points_award(db: AsyncSession, row) -> None:
    """发放积分。幂等：point_ledger 已有 EARN 则跳过。"""
    from app.models import Order
    from app.services.customer_service import CustomerService

    order = (await db.execute(select(Order).where(Order.id == int(row.aggregate_id)).with_for_update())).scalar_one_or_none()
    if not order or not order.customer_id:
        return
    await CustomerService.award_points_for_paid_order(db, order)


@register_outbox_handler("paid_side_effect", "coupon_mark_used")
async def _handle_coupon_mark_used(db: AsyncSession, row) -> None:
    """核销券/代金券。幂等：订单已退/取消或券已 USED/AVAILABLE 则 no-op。"""
    from app.models import Order
    from app.services.coupon_service import CouponService

    order = (
        await db.execute(select(Order).where(Order.id == int(row.aggregate_id)).with_for_update())
    ).scalar_one_or_none()
    if not order:
        return
    await CouponService.reconcile_for_order(db, order)


@register_outbox_handler("paid_side_effect", "pref_update")
async def _handle_pref_update(db: AsyncSession, row) -> None:
    """重算口味偏好。幂等：基于订单历史重算，重复执行收敛到同一结果。"""
    from app.models import Order
    from app.services.customer_service import PreferenceService

    order = await db.get(Order, int(row.aggregate_id))
    if not order or not order.customer_id:
        return
    await PreferenceService.update_from_order(db, order.id)
    # 智能推荐：补记 ORDER_PAID 事件并即时增量学习（event_id 幂等，重试安全）
    from app.services.taste_learning_service import TasteLearningService

    try:
        await TasteLearningService.record_paid_order_events(db, order.id)
    except Exception:
        logger.exception("推荐画像支付学习失败 order_id=%s", order.id)


@register_outbox_handler("paid_side_effect", "milestone_coupon")
async def _handle_milestone_coupon(db: AsyncSession, row) -> None:
    """累计成单里程碑自动发券。幂等：client_request_id=MILESTONE:{cid}:{sid}:{threshold}。"""
    from app.models import Order
    from app.services.coupon_service import CouponService

    order = await db.get(Order, int(row.aggregate_id))
    if not order or not order.customer_id:
        return
    await CouponService.issue_milestones_after_paid(db, order)


@register_outbox_handler("refund", "milestone_reclaim")
async def _handle_milestone_reclaim(db: AsyncSession, row) -> None:
    """全额退款后回收超档里程碑券。幂等：status_reason=milestone_reclaim。"""
    from app.models import Order, PaymentRefund
    from app.services.coupon_service import CouponService

    refund = await db.get(PaymentRefund, int(row.aggregate_id))
    if not refund:
        return
    order = await db.get(Order, refund.order_id)
    if not order or not order.customer_id:
        return
    await CouponService.reclaim_milestones_after_full_refund(
        db, order, refund_id=int(refund.id)
    )


async def run_paid_side_effects(db: AsyncSession, order, *, do_pref: bool = True) -> None:
    """[已废弃] 同步执行所有副作用；保留仅供过渡期兼容，主路径已改 outbox。

    新代码应调用 enqueue_paid_side_effects 与支付 PAID 同事务写入 outbox。
    """
    from app.services.coupon_service import CouponService
    from app.services.customer_service import CustomerService, PreferenceService

    try:
        await CustomerService.award_points_for_paid_order(db, order)
    except Exception:
        logger.exception("积分发放失败 order_id=%s", order.id)
    try:
        await CouponService.mark_used(db, order)
    except Exception:
        logger.exception("券核销失败 order_id=%s", order.id)
    try:
        await CouponService.issue_milestones_after_paid(db, order)
    except Exception:
        logger.exception("里程碑发券失败 order_id=%s", order.id)
    if do_pref:
        try:
            await PreferenceService.update_from_order(db, order.id)
        except Exception:
            logger.exception("口味记录失败 order_id=%s", order.id)


# ---------- 退款补偿 outbox handlers（#3 分布式事务补偿）----------


@register_outbox_handler("refund", "points_deduct_refund")
async def _handle_points_deduct_refund(db: AsyncSession, row) -> None:
    """退款成功后扣减积分。幂等：point_ledger 按 refund_id 去重。

    aggregate_id = refund_id；provider 已退款但本地积分扣减失败时由 _finalize_refund 入队。
    """
    from app.models import Order, PaymentRefund
    from app.services.customer_service import CustomerService

    refund = await db.get(PaymentRefund, int(row.aggregate_id))
    if not refund:
        # refund 行尚未落库或已被清理；no-op，等下次重试
        return
    order = await db.get(Order, refund.order_id)
    if not order or not order.customer_id:
        return
    await CustomerService.deduct_points_for_refund(
        db, order, refund.amount, refund_id=refund.id
    )


@register_outbox_handler("refund", "pref_recompute")
async def _handle_pref_recompute(db: AsyncSession, row) -> None:
    """退款成功后重算口味。幂等：基于全量订单历史重算，重复执行收敛到同一结果。

    aggregate_id = refund_id；通过 refund → order → customer_id 定位顾客。
    """
    from app.models import Order, PaymentRefund
    from app.services.customer_service import PreferenceService

    refund = await db.get(PaymentRefund, int(row.aggregate_id))
    if not refund:
        return
    order = await db.get(Order, refund.order_id)
    if not order or not order.customer_id:
        return
    await PreferenceService.recompute_for_customer(db, order.customer_id)
    # 智能推荐：整单退款记负向事件（幂等）；部分退款由既有偏好降权覆盖
    from app.services.taste_learning_service import TasteLearningService

    try:
        await TasteLearningService.record_full_refund_events(db, order.id)
    except Exception:
        logger.exception("推荐画像退款学习失败 order_id=%s", order.id)


@register_outbox_handler("order", "coupon_release")
async def _handle_coupon_release(db: AsyncSession, row) -> None:
    """取消/全额退款后回滚优惠券。幂等：release_for_order 行锁 + 状态守卫。

    aggregate_id = order_id。主路径同步 release 失败时入队补偿。
    """
    from app.models import Order
    from app.services.coupon_service import CouponService

    order = (
        await db.execute(select(Order).where(Order.id == int(row.aggregate_id)).with_for_update())
    ).scalar_one_or_none()
    if not order:
        return
    await CouponService.reconcile_for_order(db, order)


async def enqueue_coupon_release(db: AsyncSession, order_id: int) -> None:
    """入队订单券回滚补偿（与主事务同提交）。"""
    try:
        await enqueue_outbox(
            db,
            aggregate_type="order",
            aggregate_id=int(order_id),
            event_kind="coupon_release",
            payload={"order_id": int(order_id)},
        )
    except Exception:
        logger.exception("入队 coupon_release 失败 order_id=%s", order_id)
