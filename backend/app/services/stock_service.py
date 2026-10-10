"""库存原子扣减 / 回滚：优先分店库存，无覆盖则用品牌商品库存"""
from __future__ import annotations

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import dumps_json
from app.models import OutboxEvent, Product, StoreProductStock
from app.services.outbox_worker import enqueue_outbox

# stock=999 视为不限库存
UNLIMITED_STOCK = 999


async def _emit_stock_mirror(
    db: AsyncSession,
    *,
    store_id: int,
    product_id: int,
    mirror_id: int,
    zero: bool,
) -> None:
    """库存跨 0 翻转时发出外卖镜像信号（同事务）。

    复用 outbox：aggregate 用门店库存行 id（每店每品唯一）。因同一商品会反复
    售罄/补货，而 outbox 键永久唯一，信号行为 DONE 时将其复活为 PENDING；
    handler 执行时重读最新库存再推送，乱序/重复均收敛到最新值，天然幂等。
    """
    payload = {"store_id": store_id, "product_id": product_id, "zero": zero}
    _, is_new = await enqueue_outbox(
        db,
        aggregate_type="delivery_stock",
        aggregate_id=int(mirror_id),
        event_kind="mirror",
        payload=payload,
    )
    if not is_new:
        await db.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == "delivery_stock",
                OutboxEvent.aggregate_id == int(mirror_id),
                OutboxEvent.event_kind == "mirror",
            )
            .values(
                status="PENDING",
                attempt_count=0,
                next_retry_at=None,
                locked_at=None,
                locked_by=None,
                last_error=None,
                payload=dumps_json(payload),
            )
        )


async def _lock_product_store(db: AsyncSession, product_id: int) -> int | None:
    """锁定品牌商品行并返回其 store_id。锁定读看最新已提交数据，避免快照把新库存行看成不存在。"""
    row = (
        await db.execute(
            select(Product.store_id).where(Product.id == int(product_id)).with_for_update()
        )
    ).first()
    if row is None or row[0] is None:
        return None
    return int(row[0])


async def _lock_store_row(db: AsyncSession, store_id: int, product_id: int) -> StoreProductStock | None:
    return (
        await db.execute(
            select(StoreProductStock)
            .where(
                StoreProductStock.store_id == store_id,
                StoreProductStock.product_id == product_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def deduct_stock(
    db: AsyncSession,
    product_id: int,
    qty: int,
    *,
    store_id: int | None = None,
) -> None:
    # 数量用条件 UPDATE 一次完成。先锁商品行再锁门店库存行，锁定读看到最新已提交行，
    # 避免可重复读快照把刚插入的门店库存看成不存在、进而扣到别的门店商品上。
    if qty <= 0:
        raise AppException(40030, "扣减数量非法")

    if store_id:
        owner = await _lock_product_store(db, product_id)
        if owner is None or int(owner) != int(store_id):
            raise AppException(40031, f"商品不属于当前门店或不可售: {product_id}")
        row = await _lock_store_row(db, int(store_id), product_id)
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
            # 扣减成功时扣前必 >0（条件 UPDATE 保证）；若扣后归 0 即 >0→0 售罄翻转
            after = (
                await db.execute(
                    text(
                        "SELECT stock FROM store_product_stocks "
                        "WHERE store_id = :sid AND product_id = :pid"
                    ),
                    {"sid": store_id, "pid": product_id},
                )
            ).scalar()
            if int(after or 0) == 0:
                await _emit_stock_mirror(
                    db, store_id=store_id, product_id=product_id, mirror_id=row.id, zero=True
                )
            # 销量仍记在品牌商品上
            await db.execute(
                text(
                    "UPDATE products SET sales_count = sales_count + :qty "
                    "WHERE id = :pid AND store_id = :sid"
                ),
                {"pid": product_id, "qty": qty, "sid": int(store_id)},
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
              AND (:sid IS NULL OR store_id = :sid)
            """
        ),
        {
            "pid": product_id,
            "qty": qty,
            "unlimited": UNLIMITED_STOCK,
            "sid": int(store_id) if store_id else None,
        },
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
    # P1-48: Per-order idempotency is enforced by the caller (apply_order_status in
    # order_lifecycle.py), which only invokes _restore_order_stock on the FIRST transition into
    # CANCELLED/REFUNDED (guarded by `old not in ("CANCELLED", "REFUNDED")` and an early-return
    # when old == target). This function does an unconditional increment, so callers MUST guard
    # against double-restore; do not call it directly without that protection.
    if qty <= 0:
        return

    if store_id:
        owner = await _lock_product_store(db, product_id)
        if owner is None or int(owner) != int(store_id):
            return
        row = await _lock_store_row(db, int(store_id), product_id)
        if row is not None:
            was_zero = int(row.stock or 0) == 0
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
            # 回库前为 0、回库后必 >0（qty>0）：0→>0 恢复营业，镜像解除售罄
            if was_zero:
                await _emit_stock_mirror(
                    db, store_id=store_id, product_id=product_id, mirror_id=row.id, zero=False
                )
            await db.execute(
                text(
                    "UPDATE products SET sales_count = GREATEST(0, sales_count - :qty) "
                    "WHERE id = :pid AND store_id = :sid"
                ),
                {"pid": product_id, "qty": qty, "sid": int(store_id)},
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
              AND (:sid IS NULL OR store_id = :sid)
            """
        ),
        {
            "pid": product_id,
            "qty": qty,
            "unlimited": UNLIMITED_STOCK,
            "sid": int(store_id) if store_id else None,
        },
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
