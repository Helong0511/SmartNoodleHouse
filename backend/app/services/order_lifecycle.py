"""订单状态变更统一入口：校验迁移 + 写历史 + 库存/偏好/时间戳联动。"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import Order, OrderItem, Product
from app.services.order_state_machine import (
    assert_payment_transition,
    assert_staff_transition,
    assert_system_transition,
)
from app.services.status_history import record_order_status

logger = logging.getLogger(__name__)


def _default_restore_inventory(old_status: str, target_status: str) -> bool:
    """退款不等于退回餐品：仅尚未进入制作流程的订单自动恢复库存。"""
    old = (old_status or "").strip().upper()
    target = (target_status or "").strip().upper()
    if target == "CANCELLED":
        return True
    if target == "REFUNDED":
        return old in ("PENDING_PAYMENT", "PAID", "CONFIRMED")
    return False



async def order_all_no_cooking(db: AsyncSession, order_id: int) -> bool:
    """订单内全部明细均为免烹饪餐品（如纯卤味单）。"""
    items = list((await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))).scalars().all())
    if not items:
        return False
    product_ids = list({int(i.product_id) for i in items})
    rows = (
        await db.execute(select(Product.id, Product.no_cooking).where(Product.id.in_(product_ids)))
    ).all()
    flags = {int(pid): bool(nc) for pid, nc in rows}
    return all(flags.get(int(i.product_id), False) for i in items)


async def apply_order_status(
    db: AsyncSession,
    order: Order,
    target: str,
    *,
    source: str,
    operator_id: int | None = None,
    remark: str | None = None,
    restore_inventory: bool | None = None,
    update_preferences: bool | None = None,
    payment_status: str | None = None,
    assert_kind: str | None = None,
) -> Order:
    """将订单迁到 target，并联动必要副作用。

    assert_kind: STAFF | PAYMENT | SYSTEM；默认按 source 推断。
    restore_inventory: None 时取消单自动回库存；退款单仅在 PENDING_PAYMENT/PAID/CONFIRMED 等未开制作状态回库存。
    update_preferences: None 时对 PAID 自动累计偏好（支付成功即记录所选规格）。
    """
    target = (target or "").strip().upper()
    if not target:
        raise AppException(40021, "目标状态为空")

    # 以当前读（FOR UPDATE）取权威状态并串行化并发迁移：支付退款、平台取消、
    # 商家取消等可能在不同事务里基于同一 RR 快照双双通过 Python 校验，若不加行锁，
    # 后提交方会覆写终态（REFUNDED→CANCELLED）并因旧快照非终态而二次回补库存（虚增）。
    # 此处只取标量，不刷新调用方传入的 ORM 对象；已持锁的调用方在同一事务内重复加锁无副作用。
    current = (
        await db.execute(select(Order.status).where(Order.id == order.id).with_for_update())
    ).scalar_one_or_none()
    if current is not None:
        old = (current or "").strip().upper()
    else:
        # 尚未落库的新单（极端路径）退回使用对象值
        old = (order.status or "").strip().upper()
    if old == target:
        if payment_status is not None and order.payment_status != payment_status:
            order.payment_status = payment_status
            await db.flush()
        return order

    kind = (assert_kind or _infer_assert_kind(source)).upper()
    if kind == "STAFF" and target == "COMPLETED" and old in ("PAID", "CONFIRMED"):
        from app.services.customer_display import is_customer_display_enabled

        has_display = await is_customer_display_enabled(
            db, int(order.store_id) if order.store_id else None
        )
        tip = (
            "请先点击「制作完成」，叫号屏提醒取餐后再出餐"
            if has_display
            else "请先点击「制作完成」后再出餐"
        )
        raise AppException(40026, tip)
    if kind == "STAFF" and target == "READY" and old in ("PAID", "CONFIRMED"):
        if not await order_all_no_cooking(db, order.id):
            raise AppException(40027, "订单含需烹饪餐品，请按「开始制作」流程操作")
    if kind == "STAFF":
        assert_staff_transition(old, target)
    elif kind == "PAYMENT":
        assert_payment_transition(old, target)
    else:
        assert_system_transition(old, target)

    order.status = target
    if payment_status is not None:
        order.payment_status = payment_status

    do_restore = bool(restore_inventory) if restore_inventory is not None else _default_restore_inventory(old, target)
    do_pref = update_preferences if update_preferences is not None else target == "PAID"

    if target == "COMPLETED" and old != "COMPLETED":
        order.completed_at = now_local()
        order.kitchen_claimed_by = None
        order.kitchen_claimed_at = None
    elif target in ("CANCELLED", "REFUNDED"):
        if old not in ("CANCELLED", "REFUNDED"):
            order.cancelled_at = order.cancelled_at or now_local()
            order.kitchen_claimed_by = None
            order.kitchen_claimed_at = None
            if do_restore:
                await _restore_order_stock(db, order)
            # 取消未支付：释放 RESERVED；全额退款：回滚 USED 券/代金券；同步冲减公益 PENDING/NORMAL 台账
            if (target == "CANCELLED" and old == "PENDING_PAYMENT") or target == "REFUNDED":
                from app.services.coupon_service import CouponService
                from app.services.paid_side_effects import enqueue_coupon_release
                from app.services.social_benefit_service import SocialBenefitService

                try:
                    await CouponService.release_for_order(db, order)
                except Exception:
                    logger.exception(
                        "释放/回滚优惠券失败，已入队补偿 order_id=%s target=%s",
                        order.id,
                        target,
                    )
                    await enqueue_coupon_release(db, int(order.id))
                try:
                    await SocialBenefitService.reverse_for_order(db, int(order.id))
                except Exception:
                    logger.exception(
                        "冲减公益台账失败 order_id=%s target=%s",
                        order.id,
                        target,
                    )
    elif target == "READY" and old != "READY":
        # Outbox 是 READY 转换的持久化组成部分；入队失败必须回滚本次状态变更，
        # 不能吞掉数据库错误后提交 READY 却缺失通知任务。
        await notify_order_ready_subscribe(db, order)
    elif target == "PAID" and old != "PAID":
        if old == "CANCELLED":
            # 关单后渠道迟到成功：取消时已回补库存，复活须重新扣库
            order.cancelled_at = None
            try:
                await _deduct_order_stock(db, order)
            except Exception:
                logger.exception(
                    "迟到支付复活扣库失败 order_id=%s，拒绝入账以免超卖", order.id
                )
                raise
        if order.customer_id:
            # 支付成功副作用：与 PAID 同事务写入 DB outbox（积分/券/口味）。
            # outbox 写入失败 → 整事务回滚（含 PAID），provider 回调重试，fail-closed。
            # 副作用实际执行由 outbox worker 异步驱动，各 handler 按 order_id 幂等。
            from app.services.paid_side_effects import enqueue_paid_side_effects

            await enqueue_paid_side_effects(db, order)

    await record_order_status(
        db,
        order_id=order.id,
        from_status=old,
        to_status=target,
        source=source[:30],
        operator_id=operator_id,
        remark=remark,
    )
    await db.flush()
    return order


async def notify_order_ready_subscribe(db: AsyncSession, order: Order) -> None:
    """订单 READY 时分别持久化微信、支付宝通知任务。

    每个平台独立事件，避免一个平台成功、另一个平台失败后重试造成前者重复通知。
    HTTP 调用由 Outbox worker 在订单事务提交后执行。
    """
    if not order or not order.customer_id or not order.id:
        return
    # 只向创建订单时实际使用的小程序渠道发送订阅通知。
    # H5/POS/外卖订单没有对应小程序订阅授权，不得因会员同时绑定两个账号而双发。
    channel = str(getattr(order, "channel", "") or "").strip().upper()
    platform = {"WECHAT": "wechat", "ALIPAY": "alipay"}.get(channel)
    if platform is None:
        return

    from app.services.outbox_worker import enqueue_outbox

    await enqueue_outbox(
        db,
        aggregate_type="order_notification",
        aggregate_id=int(order.id),
        event_kind=f"order_ready_{platform}",
        payload={"order_id": int(order.id), "channel": platform},
    )


def _infer_assert_kind(source: str) -> str:
    s = (source or "").upper()
    if s in ("STAFF", "KITCHEN"):
        return "STAFF"
    if s in ("PAYMENT", "MOCK_PAYMENT"):
        return "PAYMENT"
    return "SYSTEM"


async def _restore_order_stock(db: AsyncSession, order: Order) -> None:
    from app.services.stock_service import restore_stock

    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
    # 按 product_id 聚合数量，避免同餐品多次调用 restore_stock 产生 N+1
    aggregated: dict[int, int] = {}
    for it in items:
        pid = int(it.product_id)
        aggregated[pid] = aggregated.get(pid, 0) + int(it.quantity)
    # Stable product lock order prevents two multi-item order transitions from
    # deadlocking when their OrderItem rows are returned in different orders.
    for product_id in sorted(aggregated):
        await restore_stock(db, product_id, aggregated[product_id], store_id=order.store_id)
    if aggregated:
        from app.services.menu_cache_service import invalidate_store_menu_cache

        await invalidate_store_menu_cache(db, order.store_id)


async def _deduct_order_stock(db: AsyncSession, order: Order) -> None:
    """CANCELLED→PAID 复活时重新扣库存（与建单扣库口径一致）。"""
    from app.services.stock_service import deduct_stock

    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
    aggregated: dict[int, int] = {}
    for it in items:
        pid = int(it.product_id)
        aggregated[pid] = aggregated.get(pid, 0) + int(it.quantity)
    # Match restore/create ordering so all order lifecycle paths lock products
    # by ascending primary key, independent of cart or database row order.
    for product_id in sorted(aggregated):
        await deduct_stock(db, product_id, aggregated[product_id], store_id=order.store_id)
    if aggregated:
        from app.services.menu_cache_service import invalidate_store_menu_cache

        await invalidate_store_menu_cache(db, order.store_id)
