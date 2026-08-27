"""订单/支付状态变更历史"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrderStatusHistory, PaymentStatusHistory


async def record_order_status(
    db: AsyncSession,
    *,
    order_id: int,
    from_status: str | None,
    to_status: str,
    source: str,
    operator_id: int | None = None,
    remark: str | None = None,
) -> None:
    db.add(
        OrderStatusHistory(
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            operator_id=operator_id,
            remark=remark,
        )
    )


async def record_payment_status(
    db: AsyncSession,
    *,
    payment_id: int,
    from_status: str | None,
    to_status: str,
    source: str,
    remark: str | None = None,
) -> None:
    db.add(
        PaymentStatusHistory(
            payment_id=payment_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            remark=remark,
        )
    )
