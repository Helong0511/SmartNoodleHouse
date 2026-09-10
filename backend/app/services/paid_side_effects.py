"""支付成功后副作用（积分/券核销/口味）失败入队，由调度任务重试。"""
from __future__ import annotations

import logging

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

QUEUE_KEY = "snh:paid_side_effects"
MAX_QUEUE = 5000


async def enqueue_paid_side_effect(order_id: int) -> None:
    try:
        oid = int(order_id)
        if oid <= 0:
            return
        r = await get_redis()
        await r.lpush(QUEUE_KEY, str(oid))
        await r.ltrim(QUEUE_KEY, 0, MAX_QUEUE - 1)
    except Exception:
        logger.exception("入队支付副作用失败 order_id=%s", order_id)


async def run_paid_side_effects(db, order, *, do_pref: bool = True) -> None:
    """幂等：积分/核销内部已有防重。"""
    from app.services.coupon_service import CouponService
    from app.services.customer_service import CustomerService, PreferenceService

    errors: list[str] = []
    try:
        await CustomerService.award_points_for_paid_order(db, order)
    except Exception:
        logger.exception("积分发放失败 order_id=%s", order.id)
        errors.append("points")
    try:
        await CouponService.mark_used(db, order)
    except Exception:
        logger.exception("券核销失败 order_id=%s", order.id)
        errors.append("coupon")
    if do_pref:
        try:
            await PreferenceService.update_from_order(db, order.id)
        except Exception:
            logger.exception("口味记录失败 order_id=%s", order.id)
            errors.append("pref")
    if errors:
        await enqueue_paid_side_effect(int(order.id))
        raise RuntimeError(f"paid_side_effects_partial:{','.join(errors)}")


async def process_pending_side_effects(*, limit: int = 30) -> int:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models import Order

    try:
        r = await get_redis()
    except Exception:
        return 0

    done = 0
    for _ in range(max(1, int(limit))):
        raw = await r.rpop(QUEUE_KEY)
        if not raw:
            break
        try:
            oid = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        except (TypeError, ValueError):
            continue
        try:
            async with AsyncSessionLocal() as db:
                order = (
                    await db.execute(select(Order).where(Order.id == oid).with_for_update())
                ).scalar_one_or_none()
                if not order or not order.customer_id:
                    continue
                if (order.payment_status or "").upper() != "PAID" and (order.status or "") != "PAID":
                    # 仍可能已进入后继状态但曾付过款
                    if (order.payment_status or "").upper() not in ("PAID", "PARTIAL_REFUND", "REFUNDED"):
                        continue
                try:
                    await run_paid_side_effects(db, order, do_pref=True)
                    await db.commit()
                    done += 1
                except RuntimeError:
                    await db.commit()  # 部分成功也提交
                    # 已重新入队
                except Exception:
                    logger.exception("补偿支付副作用异常 order_id=%s", oid)
                    await db.rollback()
                    await enqueue_paid_side_effect(oid)
        except Exception:
            logger.exception("补偿支付副作用处理失败 order_id=%s", oid)
            await enqueue_paid_side_effect(oid)
    return done
