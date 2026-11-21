"""Serialization primitives for customer-owned database writes."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ACCOUNT_DISABLED
from app.core.exceptions import AppException
from app.models import Customer, CustomerWriteGate


async def lock_customer_write_gates(
    db: AsyncSession, customer_ids: Iterable[int]
) -> list[int]:
    """Lock all requested customer gates in ascending ID order.

    Gate rows are provisioned by migration 0052 and by the Customer ORM
    after_insert hook. Missing rows fail closed: request handlers must never
    lazily create a mutex row because that would race with another creator.
    """
    ids = sorted({int(value) for value in customer_ids if int(value) > 0})
    if not ids:
        return []

    result = await db.execute(
        select(CustomerWriteGate.customer_id)
        .where(CustomerWriteGate.customer_id.in_(ids))
        .order_by(CustomerWriteGate.customer_id)
        .with_for_update()
    )
    locked_ids = [int(value) for value in result.scalars().all()]
    if locked_ids != ids:
        missing = sorted(set(ids) - set(locked_ids))
        raise AppException(
            50021,
            f"顾客写入锁记录缺失（customer_id={missing}），已拒绝继续写入",
            status_code=500,
        )
    return locked_ids


async def lock_active_customer_for_write(
    db: AsyncSession, customer_id: int
) -> Customer:
    """Acquire the lifecycle gate, then current-read and validate its customer."""
    cid = int(customer_id)
    await lock_customer_write_gates(db, [cid])
    result = await db.execute(
        select(Customer)
        .where(Customer.id == cid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    customer = result.scalar_one_or_none()
    if (
        customer is None
        or int(customer.status or 0) != 1
        or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper()
        != "ACTIVE"
    ):
        raise AppException(ACCOUNT_DISABLED, "顾客不存在或已禁用", status_code=401)
    return customer
