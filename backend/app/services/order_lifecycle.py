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


async def order_all_no_cooking(db: AsyncSession, order_id: int) -> bool:
    """订单内全部明细均为免烹饪商品（如纯卤味单）。"""
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
    restore_inventory: None 时对 CANCELLED/REFUNDED 自动回库存（仅首次进入该终态）。
    update_preferences: None 时对 PAID 自动累计偏好（支付成功即记录所选规格）。
    """
    target = (target or "").strip().upper()
    if not target:
        raise AppException(40021, "目标状态为空")
    old = (order.status or "").strip().upper()
    if old == target:
        if payment_status is not None and order.payment_status != payment_status:
            order.payment_status = payment_status
            await db.flush()
        return order

    kind = (assert_kind or _infer_assert_kind(source)).upper()
    if kind == "STAFF" and target == "COMPLETED" and old in ("PAID", "CONFIRMED"):
        if not await order_all_no_cooking(db, order.id):
            raise AppException(40026, "订单含需烹饪商品，请按「开始制作」流程操作")
    if kind == "STAFF":
        assert_staff_transition(old, target)
    elif kind == "PAYMENT":
        assert_payment_transition(old, target)
    else:
        assert_system_transition(old, target)

    order.status = target
    if payment_status is not None:
        order.payment_status = payment_status

    do_restore = restore_inventory if restore_inventory is not None else target in ("CANCELLED", "REFUNDED")
    do_pref = update_preferences if update_preferences is not None else target == "PAID"

    if target == "COMPLETED" and old != "COMPLETED":
        order.completed_at = now_local()
        if old in ("PAID", "CONFIRMED"):
            try:
                await notify_order_ready_subscribe(db, order)
            except Exception:
                logger.exception("直出餐订阅消息推送失败 order_id=%s", order.id)
    elif target in ("CANCELLED", "REFUNDED"):
        if old not in ("CANCELLED", "REFUNDED"):
            order.cancelled_at = order.cancelled_at or now_local()
            if do_restore:
                await _restore_order_stock(db, order)
            if target == "CANCELLED" and old == "PENDING_PAYMENT":
                try:
                    from app.services.coupon_service import CouponService

                    await CouponService.release_for_order(db, order)
                except Exception:
                    logger.exception("释放预占券失败 order_id=%s", order.id)
    elif target == "READY" and old != "READY":
        try:
            await notify_order_ready_subscribe(db, order)
        except Exception:
            logger.exception("出餐订阅消息推送失败 order_id=%s", order.id)
    elif target == "PAID" and old != "PAID" and order.customer_id:
        try:
            from app.services.customer_service import CustomerService, PreferenceService
            from app.services.coupon_service import CouponService

            await CustomerService.award_points_for_paid_order(db, order)
            await CouponService.mark_used(db, order)
            if do_pref:
                await PreferenceService.update_from_order(db, order.id)
        except Exception:
            logger.exception("积分发放、券核销或口味记录失败 order_id=%s", order.id)

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
    """订单进入 READY 时尝试推送订阅消息（失败不阻断主流程）。"""
    if not order.customer_id:
        return
    from app.models import CustomerWechatAccount, DiningTable
    from app.services.wechat_mini_client import send_order_ready_subscribe

    openid = (
        await db.execute(
            select(CustomerWechatAccount.openid).where(CustomerWechatAccount.customer_id == order.customer_id)
        )
    ).scalar_one_or_none()
    if not openid:
        return
    table_label = None
    if order.table_id:
        t = await db.get(DiningTable, order.table_id)
        if t:
            table_label = t.table_name or t.table_no
    await send_order_ready_subscribe(
        openid=openid,
        order_no=order.order_no,
        order_id=order.id,
        table_label=table_label,
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
    for it in items:
        await restore_stock(db, it.product_id, it.quantity, store_id=order.store_id)
