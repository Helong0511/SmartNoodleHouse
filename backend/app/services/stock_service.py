"""库存原子扣减 / 回滚 + sales_count"""
from __future__ import annotations

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import Product

# stock=999 视为不限库存
UNLIMITED_STOCK = 999


async def deduct_stock(db: AsyncSession, product_id: int, qty: int) -> None:
    if qty <= 0:
        raise AppException(40030, "扣减数量非法")
    # 原子：仅当库存足够或不限时更新
    result = await db.execute(
        text(
            """
            UPDATE products
            SET stock = CASE
                  WHEN stock >= :unlimited THEN stock
                  ELSE stock - :qty
                END,
                sales_count = sales_count + :qty
            WHERE id = :pid
              AND status = 'ON_SALE'
              AND (stock >= :unlimited OR stock >= :qty)
            """
        ),
        {"pid": product_id, "qty": qty, "unlimited": UNLIMITED_STOCK},
    )
    if result.rowcount != 1:
        raise AppException(40031, f"库存不足或商品不可售: {product_id}")


async def restore_stock(db: AsyncSession, product_id: int, qty: int) -> None:
    if qty <= 0:
        return
    await db.execute(
        text(
            """
            UPDATE products
            SET stock = CASE
                  WHEN stock >= :unlimited THEN stock
                  ELSE stock + :qty
                END,
                sales_count = GREATEST(0, sales_count - :qty)
            WHERE id = :pid
            """
        ),
        {"pid": product_id, "qty": qty, "unlimited": UNLIMITED_STOCK},
    )


async def deduct_for_order_items(db: AsyncSession, items: list[dict]) -> None:
    """items: [{product_id, quantity}, ...] 按 product_id 聚合后扣减"""
    merged: dict[int, int] = {}
    for it in items:
        pid = int(it["product_id"])
        merged[pid] = merged.get(pid, 0) + int(it["quantity"])
    for pid, qty in merged.items():
        await deduct_stock(db, pid, qty)
