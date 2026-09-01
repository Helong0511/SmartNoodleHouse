"""厨房 KDS 软认领：开始制作写入认领人；他人需接管后才能推进。"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import Order, User

# 进入厨房流水后会认领（含免烹饪直达 READY）
CLAIM_ON_TARGETS = frozenset({"PREPARING", "COOKING", "READY"})
CLAIM_FROM_STATUSES = frozenset({"PAID", "CONFIRMED", "PREPARING", "COOKING", "READY"})


async def claimer_display_name(db: AsyncSession, user_id: int | None) -> str:
    if not user_id:
        return ""
    user = await db.get(User, int(user_id))
    if not user:
        return f"#{user_id}"
    return (user.display_name or user.username or f"#{user_id}").strip()


async def apply_kitchen_claim_for_status(
    db: AsyncSession,
    order: Order,
    target: str,
    *,
    operator_id: int | None,
    force_takeover: bool = False,
) -> None:
    """在员工推进厨房状态前处理认领。

    - 未认领 → 写入当前操作人
    - 本人已认领 → 放行
    - 他人已认领 → 需 force_takeover，否则 40028
    """
    target = (target or "").strip().upper()
    if not operator_id:
        return
    if target not in CLAIM_ON_TARGETS and target not in ("COMPLETED", "CANCELLED"):
        return
    old = (order.status or "").strip().upper()
    if old not in CLAIM_FROM_STATUSES and target not in CLAIM_ON_TARGETS:
        return

    claimer = order.kitchen_claimed_by
    if claimer and int(claimer) != int(operator_id):
        name = await claimer_display_name(db, int(claimer))
        if not force_takeover:
            raise AppException(40028, f"订单已被「{name}」认领，请先点击「接管」")
        order.kitchen_claimed_by = int(operator_id)
        order.kitchen_claimed_at = now_local()
        return

    if not claimer and target in CLAIM_ON_TARGETS:
        order.kitchen_claimed_by = int(operator_id)
        order.kitchen_claimed_at = now_local()


async def takeover_kitchen_claim(
    db: AsyncSession,
    order: Order,
    *,
    operator_id: int,
) -> Order:
    """仅接管认领，不改订单状态。"""
    if order.status not in CLAIM_FROM_STATUSES:
        raise AppException(40029, "当前订单状态不可接管")
    claimer = order.kitchen_claimed_by
    if claimer and int(claimer) == int(operator_id):
        return order
    order.kitchen_claimed_by = int(operator_id)
    order.kitchen_claimed_at = now_local()
    await db.flush()
    return order
