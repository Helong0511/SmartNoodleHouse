"""厨房 KDS 软认领：开始制作写入认领人；他人需接管后才能推进。"""
from __future__ import annotations

from sqlalchemy import update
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

    - 未认领 → 条件 UPDATE 抢占（WHERE 认领人为空），并发下仅一人成功
    - 本人已认领 → 放行
    - 他人已认领 → 需 force_takeover（条件 UPDATE WHERE 认领人=原认领者），否则 40028
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
    if claimer and int(claimer) == int(operator_id):
        return

    if claimer and not force_takeover:
        name = await claimer_display_name(db, int(claimer))
        raise AppException(40028, f"订单已被「{name}」认领，请先点击「接管」")

    # 抢占式认领：条件 UPDATE 防并发互相覆盖
    cond = (
        Order.kitchen_claimed_by.is_(None)
        if not claimer
        else Order.kitchen_claimed_by == int(claimer)
    )
    stmt = (
        update(Order)
        .where(Order.id == int(order.id), cond)
        .values(kitchen_claimed_by=int(operator_id), kitchen_claimed_at=now_local())
    )
    result = await db.execute(stmt)
    if not result.rowcount:
        # 并发下认领人已变化：按他人已认领处理
        await db.refresh(order)
        if order.kitchen_claimed_by and int(order.kitchen_claimed_by) != int(operator_id):
            name = await claimer_display_name(db, int(order.kitchen_claimed_by))
            raise AppException(40028, f"订单已被「{name}」认领，请先点击「接管」")
        return
    order.kitchen_claimed_by = int(operator_id)
    order.kitchen_claimed_at = now_local()


async def takeover_kitchen_claim(
    db: AsyncSession,
    order: Order,
    *,
    operator_id: int,
) -> Order:
    """仅接管认领，不改订单状态。条件 UPDATE 抢占，防止并发接管互相覆盖。"""
    if order.status not in CLAIM_FROM_STATUSES:
        raise AppException(40029, "当前订单状态不可接管")
    claimer = order.kitchen_claimed_by
    if claimer and int(claimer) == int(operator_id):
        return order
    # 认领接管必须基于读取时的旧认领人做 CAS 条件更新。
    # 若两名员工同时接管，只有第一个匹配旧值的请求成功，后到请求不能静默覆盖。
    expected_claimer = int(claimer) if claimer else None
    claimer_condition = (
        Order.kitchen_claimed_by.is_(None)
        if expected_claimer is None
        else Order.kitchen_claimed_by == expected_claimer
    )
    stmt = (
        update(Order)
        .where(
            Order.id == int(order.id),
            Order.status.in_(CLAIM_FROM_STATUSES),
            claimer_condition,
        )
        .values(kitchen_claimed_by=int(operator_id), kitchen_claimed_at=now_local())
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    if result.rowcount != 1:
        # 更新失败说明状态或认领人已被其他事务改变；刷新后给出明确冲突，
        # 不向前端返回“接管成功”但实际认领人却是另一人的假成功。
        await db.refresh(order)
        if order.status not in CLAIM_FROM_STATUSES:
            raise AppException(40029, "订单状态已变化，当前不可接管，请刷新后重试")
        if order.kitchen_claimed_by and int(order.kitchen_claimed_by) == int(operator_id):
            return order
        name = await claimer_display_name(db, order.kitchen_claimed_by)
        detail = f"订单已被「{name}」接管" if name else "订单认领状态已变化"
        raise AppException(40028, f"{detail}，请刷新后重试")

    order.kitchen_claimed_by = int(operator_id)
    order.kitchen_claimed_at = now_local()
    return order
