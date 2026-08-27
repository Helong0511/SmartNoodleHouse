"""库存原子扣减 / 回滚：优先分店库存，无覆盖则用品牌商品库存"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.models import StoreProductStock

# stock=999 视为不限库存
UNLIMITED_STOCK = 999


async def _has_store_row(db: AsyncSession, store_id: int, product_id: int) -> StoreProductStock | None:
    return (
        await db.execute(
            select(StoreProductStock).where(
                StoreProductStock.store_id == store_id,
                StoreProductStock.product_id == product_id,
            )
        )
    ).scalar_one_or_none()


async def deduct_stock(
    db: AsyncSession,
    product_id: int,
    qty: int,
    *,
    store_id: int | None = None,
) -> None:
    if qty <= 0:
        raise AppException(40030, "扣减数量非法")

    if store_id:
        row = await _has_store_row(db, store_id, product_id)
        if row is not None:
            if int(row.status) != 1:
                raise AppException(40031, f"本店未上架该商品: {product_id}")
            result = await db.execute(
                text(
                    """
                    UPDATE store_product_stocks
                    SET stock = CASE
                          WHEN stock >= :unlimited THEN stock
                          ELSE stock - :qty
                        END
                    WHERE store_id = :sid AND product_id = :pid
                      AND status = 1
                      AND (stock >= :unlimited OR stock >= :qty)
                    """
                ),
                {"sid": store_id, "pid": product_id, "qty": qty, "unlimited": UNLIMITED_STOCK},
            )
            if result.rowcount != 1:
                raise AppException(40031, f"本店库存不足或商品不可售: {product_id}")
            # 销量仍记在品牌商品上
            await db.execute(
                text("UPDATE products SET sales_count = sales_count + :qty WHERE id = :pid"),
                {"pid": product_id, "qty": qty},
            )
            return

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


async def restore_stock(
    db: AsyncSession,
    product_id: int,
    qty: int,
    *,
    store_id: int | None = None,
) -> None:
    if qty <= 0:
        return

    if store_id:
        row = await _has_store_row(db, store_id, product_id)
        if row is not None:
            await db.execute(
                text(
                    """
                    UPDATE store_product_stocks
                    SET stock = CASE
                          WHEN stock >= :unlimited THEN stock
                          ELSE stock + :qty
                        END
                    WHERE store_id = :sid AND product_id = :pid
                    """
                ),
                {"sid": store_id, "pid": product_id, "qty": qty, "unlimited": UNLIMITED_STOCK},
            )
            await db.execute(
                text(
                    "UPDATE products SET sales_count = GREATEST(0, sales_count - :qty) WHERE id = :pid"
                ),
                {"pid": product_id, "qty": qty},
            )
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


async def deduct_for_order_items(
    db: AsyncSession,
    items: list[dict],
    *,
    store_id: int | None = None,
) -> None:
    """items: [{product_id, quantity}, ...] 按 product_id 聚合后扣减"""
    merged: dict[int, int] = {}
    for it in items:
        pid = int(it["product_id"])
        merged[pid] = merged.get(pid, 0) + int(it["quantity"])
    for pid, qty in merged.items():
        await deduct_stock(db, pid, qty, store_id=store_id)
