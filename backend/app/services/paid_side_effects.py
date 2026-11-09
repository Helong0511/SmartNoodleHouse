"""支付成功后副作用（积分/券核销/口味/里程碑发券）：DB 事务型 outbox，不依赖 Redis。

支付 PAID 同事务写入 outbox 事件：
  - points_award       → CustomerService.award_points_for_paid_order
  - coupon_mark_used   → CouponService.mark_used
  - pref_update        → PreferenceService.update_from_order
  - taste_learning     → TasteLearningService.record_paid_order_events
  - milestone_coupon   → CouponService.issue_milestones_after_paid

各副作用函数自身按 order_id / 幂等键去重，故 outbox 重试不会重复入账。
"""
from __future__ import annotations

import json
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
    for kind in (
        "points_award",
        "coupon_mark_used",
        "pref_update",
        "taste_learning",
        "milestone_coupon",
        "social_benefit_confirm",
        "campaign_attribution",
    ):
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


@register_outbox_handler("order_notification", "order_ready_wechat")
@register_outbox_handler("order_notification", "order_ready_alipay")
async def _handle_order_ready_notification(db: AsyncSession, row) -> None:
    """订单 READY 通知：微信/支付宝拆分事件，互不影响重试。"""
    from app.models import DiningTable, Order
    from app.services.customer_service import CustomerService

    channel = str(getattr(row, "event_kind", "") or "").removeprefix("order_ready_")
    if channel not in {"wechat", "alipay"}:
        raise ValueError("unsupported order notification channel")

    order = await db.get(Order, int(row.aggregate_id))
    if not order or not order.customer_id or str(order.status or "").upper() != "READY":
        return

    table_label = None
    if order.table_id:
        table = await db.get(DiningTable, int(order.table_id))
        if table:
            table_label = table.table_name or table.table_no

    if channel == "wechat":
        openid = await CustomerService.resolve_wechat_openid(db, int(order.customer_id))
        if not openid:
            return
        from app.services.wechat_mini_client import send_order_ready_subscribe

        sent = await send_order_ready_subscribe(
            openid=openid,
            order_no=order.order_no,
            order_id=int(order.id),
            table_label=table_label,
        )
        if not sent:
            logger.warning("微信出餐订阅消息未发送或被平台拒绝 order_id=%s", order.id)
        return

    alipay_uid = await CustomerService.resolve_alipay_user_id(db, int(order.customer_id))
    if not alipay_uid:
        return
    from app.services.alipay_mini_client import (
        send_order_ready_subscribe as send_alipay_order_ready,
    )

    sent = await send_alipay_order_ready(
        alipay_user_id=alipay_uid,
        order_no=order.order_no,
        order_id=int(order.id),
        table_label=table_label,
    )
    if not sent:
        logger.warning("支付宝出餐订阅消息未发送或被平台拒绝 order_id=%s", order.id)


@register_outbox_handler("paid_side_effect", "points_award")
async def _handle_points_award(db: AsyncSession, row) -> None:
    """发放积分。幂等：point_ledger 已有 EARN 则跳过；全额退/取消不再发。"""
    from app.models import Order
    from app.services.customer_service import CustomerService

    # award 内部会再锁订单并校验状态（含迟到 outbox vs 退款）
    order = await db.get(Order, int(row.aggregate_id))
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


@register_outbox_handler("paid_side_effect", "taste_learning")
async def _handle_taste_learning(db: AsyncSession, row) -> None:
    """支付后的推荐画像学习独立重试；失败必须向 Outbox 传播。"""
    from app.models import Order
    from app.services.taste_learning_service import TasteLearningService

    order = await db.get(Order, int(row.aggregate_id))
    if not order or not order.customer_id:
        return
    # 事件写入以 event_id 幂等；异常不吞掉，由 Outbox 退避重试并最终进入死信。
    await TasteLearningService.record_paid_order_events(db, order.id)


@register_outbox_handler("paid_side_effect", "milestone_coupon")
async def _handle_milestone_coupon(db: AsyncSession, row) -> None:
    """累计成单里程碑自动发券。幂等：client_request_id=MILESTONE:{cid}:{sid}:{threshold}。"""
    from app.models import Order
    from app.services.coupon_service import CouponService

    order = (
        await db.execute(select(Order).where(Order.id == int(row.aggregate_id)).with_for_update())
    ).scalar_one_or_none()
    if not order or not order.customer_id:
        return
    st = (order.status or "").upper()
    pay = (order.payment_status or "").upper()
    if st in ("CANCELLED", "REFUNDED") or pay in ("REFUNDED", "CANCELLED", "UNPAID"):
        return
    await CouponService.issue_milestones_after_paid(db, order)


@register_outbox_handler("paid_side_effect", "social_benefit_confirm")
async def _handle_social_benefit_confirm(db: AsyncSession, row) -> None:
    """支付成功将公益台账 PENDING→NORMAL。幂等：已 NORMAL 则跳过。"""
    from app.services.social_benefit_service import SocialBenefitService

    await SocialBenefitService.confirm_paid(db, int(row.aggregate_id))


@register_outbox_handler("paid_side_effect", "campaign_attribution")
async def _handle_campaign_attribution(db: AsyncSession, row) -> None:
    """支付成功写活动归因（券优先）。幂等：uk_ca_idem。"""
    from app.services.attribution_service import AttributionService

    await AttributionService.attribute_paid_order(db, int(row.aggregate_id))




@register_outbox_handler("refund", "social_benefit_reverse")
async def _handle_social_benefit_reverse(db: AsyncSession, row) -> None:
    """全额退款后冲减公益核销台账。幂等：只转换 PENDING/NORMAL 状态。

    aggregate_id = refund_id；先锁订单以串行化同一订单的退款后处理。
    """
    from app.models import Order, PaymentRefund
    from app.services.social_benefit_service import SocialBenefitService

    refund = await db.get(PaymentRefund, int(row.aggregate_id))
    if not refund:
        return
    order = (
        await db.execute(select(Order).where(Order.id == int(refund.order_id)).with_for_update())
    ).scalar_one_or_none()
    if not order:
        return
    await SocialBenefitService.reverse_for_order(db, int(order.id))


@register_outbox_handler("refund", "campaign_attribution_reverse")
async def _handle_campaign_attribution_reverse(db: AsyncSession, row) -> None:
    """全额退款后冲减营销归因。幂等：只转换 ACTIVE 归因，重复执行为 no-op。

    aggregate_id = refund_id；先锁订单以串行化同一订单的退款后处理。
    """
    from app.models import Order, PaymentRefund
    from app.services.attribution_service import AttributionService

    refund = await db.get(PaymentRefund, int(row.aggregate_id))
    if not refund:
        return
    order = (
        await db.execute(select(Order).where(Order.id == int(refund.order_id)).with_for_update())
    ).scalar_one_or_none()
    if not order:
        return
    await AttributionService.reverse_for_order(db, int(order.id))


@register_outbox_handler("refund", "milestone_reclaim")
async def _handle_milestone_reclaim(db: AsyncSession, row) -> None:
    """全额退款后回收超档里程碑券。锁序 Order -> PaymentRefund -> Customer。"""
    from app.models import Order, PaymentRefund
    from app.services.coupon_service import CouponService

    payload = json.loads(row.payload) if isinstance(row.payload, str) else (row.payload or {})
    order_id = int((payload or {}).get("order_id") or 0)
    if order_id <= 0:
        raise ValueError("milestone_reclaim payload missing order_id")

    # 先锁订单，保持与支付/退款状态机一致的 Order-first 锁顺序。
    order = (
        await db.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not order or not order.customer_id:
        return
    refund = (
        await db.execute(
            select(PaymentRefund)
            .where(
                PaymentRefund.id == int(row.aggregate_id),
                PaymentRefund.order_id == order_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not refund or str(refund.status or "").upper() != "SUCCESS":
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

@register_outbox_handler("late_payment", "auto_refund")
async def _handle_late_payment_auto_refund(db: AsyncSession, row) -> None:
    """取消订单迟到收款自动原路退款；独立会话执行渠道请求，固定幂等键防重复。"""
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models import Order, Payment, PaymentRefund
    from app.services.payment_service import PaymentService

    payment_id = int(row.aggregate_id)
    payload = json.loads(row.payload) if isinstance(row.payload, str) else (row.payload or {})
    if not isinstance(payload, dict):
        raise RuntimeError(f"迟到支付 Outbox payload 格式非法 event_id={row.id}")
    if int(payload.get("payment_id") or 0) != payment_id:
        raise RuntimeError(f"迟到支付 Outbox payment_id 与 aggregate_id 不一致 event_id={row.id}")
    expected_order_id = int(payload.get("order_id") or 0)
    if expected_order_id <= 0:
        raise RuntimeError(f"迟到支付 Outbox 缺少有效 order_id event_id={row.id}")
    reason = str(payload.get("reason") or "订单已取消，迟到支付自动退款")[:255]
    idem_key = f"late-payment-refund:{payment_id}"

    # 退款申请自身会 commit 并访问支付渠道，必须使用独立会话，不能提交/释放
    # Outbox worker 当前持有的 claim 事务；外层只有在本 handler 正常返回后才标 DONE。
    async with AsyncSessionLocal() as refund_db:
        payment = await refund_db.get(Payment, payment_id)
        if not payment:
            raise RuntimeError(f"迟到支付对应的支付单不存在 payment_id={payment_id}")
        if int(payment.order_id or 0) != expected_order_id:
            raise RuntimeError(f"迟到支付 Outbox 与支付单订单归属不一致 payment_id={payment_id}")
        order = await refund_db.get(Order, expected_order_id)
        if not order:
            raise RuntimeError(f"迟到支付对应的订单不存在 payment_id={payment_id}")
        if str(order.status or "").upper() not in {"CANCELLED", "REFUNDED"}:
            raise RuntimeError(
                f"迟到支付自动退款要求订单保持取消/退款终态 order_id={expected_order_id}"
            )

        existing = (
            await refund_db.execute(
                select(PaymentRefund).where(PaymentRefund.idempotency_key == idem_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                int(existing.payment_id) != payment_id
                or int(existing.order_id) != expected_order_id
            ):
                raise RuntimeError(f"迟到支付退款幂等键关联对象不匹配 payment_id={payment_id}")
            status = str(existing.status or "").upper()
            if status in {"SUCCESS", "PENDING", "PROCESSING", "UNKNOWN"}:
                # SUCCESS 是完成；其余状态交由退款回调/查单补偿，不可另开退款单。
                return
            # FAILED/CLOSED/ABNORMAL 不能当作退款成功；保留 Outbox 失败告警供人工核验。
            raise RuntimeError(
                f"迟到支付自动退款已存在终态 {status}，需人工核验 payment_id={payment_id}"
            )

        if str(payment.status or "").upper() == "REFUNDED":
            return
        if str(payment.status or "").upper() not in {"PAID", "PARTIAL_REFUND"}:
            raise RuntimeError(
                f"迟到支付单状态不可退款 status={payment.status} payment_id={payment_id}"
            )

        inflight = await refund_db.scalar(
            select(PaymentRefund.id).where(
                PaymentRefund.payment_id == payment_id,
                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
            ).limit(1)
        )
        if inflight is not None:
            # 可能已有人工退款申请；先等待它由渠道回调/查单收敛，再计算剩余可退金额。
            raise RuntimeError(f"支付单已有退款处理中，等待收敛 payment_id={payment_id}")

        refundable = await PaymentService.refundable_amount(refund_db, payment)
        if refundable <= 0:
            return
        refund = await PaymentService.refund(
            refund_db,
            payment,
            refundable,
            reason,
            idempotency_key=idem_key,
        )
        final_status = str(refund.status or "").upper()
        if final_status in {"FAILED", "CLOSED", "ABNORMAL"}:
            raise RuntimeError(
                f"迟到支付渠道退款未成功 status={final_status} payment_id={payment_id}"
            )
        if final_status not in {"SUCCESS", "PENDING", "PROCESSING", "UNKNOWN"}:
            raise RuntimeError(
                f"迟到支付渠道退款返回未知状态 status={final_status} payment_id={payment_id}"
            )

