"""历史订单冷库归档：将超期终态订单写入冷库快照（物理删热表需二期解耦 FK）。"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local, serialize_dt
from app.models import ArchivedOrderSnapshot, Order, OrderItem, OrderItemOption, Payment

_ARCHIVEABLE_STATUSES = frozenset(
    {"COMPLETED", "CANCELLED", "REFUNDED", "CLOSED", "DONE"}
)


class OrderArchiveService:
    @staticmethod
    async def _snapshot_payload(db: AsyncSession, order: Order) -> dict[str, Any]:
        items_db = list(
            (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars().all()
        )
        items = []
        for it in items_db:
            opts = list(
                (
                    await db.execute(
                        select(OrderItemOption).where(OrderItemOption.order_item_id == it.id)
                    )
                ).scalars().all()
            )
            items.append(
                {
                    "product_id": it.product_id,
                    "product_name": it.product_name_snapshot,
                    "quantity": it.quantity,
                    "unit_price": str(it.unit_price_snapshot),
                    "options": [
                        {
                            "option_name": o.option_name_snapshot,
                            "extra_price": str(o.extra_price_snapshot or "0"),
                        }
                        for o in opts
                    ],
                }
            )
        pays = list(
            (await db.execute(select(Payment).where(Payment.order_id == order.id))).scalars().all()
        )
        return {
            "order": {
                "id": order.id,
                "order_no": order.order_no,
                "store_id": order.store_id,
                "status": order.status,
                "payment_status": order.payment_status,
                "total_amount": str(order.total_amount),
                "created_at": serialize_dt(order.created_at),
            },
            "items": items,
            "payments": [
                {
                    "payment_no": p.payment_no,
                    "provider": p.provider,
                    "amount": str(p.amount),
                    "status": p.status,
                }
                for p in pays
            ],
        }

    @staticmethod
    async def archive_older_than(
        db: AsyncSession,
        *,
        older_than_days: int = 365,
        limit: int = 200,
        dry_run: bool = False,
        store_id: int | None = None,
    ) -> dict[str, Any]:
        """写入 archived_order_snapshots；不删除热表（payments/ledger 等 FK 仍指向 orders）。

        store_id=None 仅供总部全平台归档；门店调用方必须先由 require_bound_store
        将用户身份解析为绑定门店 ID，再传入本方法。
        """
        days = max(30, int(older_than_days))
        cutoff = now_local() - timedelta(days=days)
        filters = [
            Order.created_at < cutoff,
            Order.status.in_(tuple(_ARCHIVEABLE_STATUSES)),
        ]
        if store_id is not None:
            filters.append(Order.store_id == int(store_id))
        orders = list(
            (
                await db.execute(
                    select(Order)
                    .where(*filters)
                    .order_by(Order.created_at.asc())
                    .limit(max(1, min(limit, 1000)))
                )
            ).scalars().all()
        )
        archived = 0
        skipped = 0
        for order in orders:
            exists = (
                await db.execute(
                    select(ArchivedOrderSnapshot.id).where(
                        ArchivedOrderSnapshot.order_id == order.id
                    )
                )
            ).scalar_one_or_none()
            if exists:
                skipped += 1
                continue
            if dry_run:
                archived += 1
                continue
            payload = await OrderArchiveService._snapshot_payload(db, order)
            db.add(
                ArchivedOrderSnapshot(
                    order_id=order.id,
                    store_id=order.store_id,
                    order_no=order.order_no,
                    status=order.status,
                    payment_status=order.payment_status,
                    original_created_at=order.created_at,
                    payload=payload,
                )
            )
            archived += 1
        if not dry_run:
            await db.flush()
        return {
            "cutoff": serialize_dt(cutoff),
            "older_than_days": days,
            "candidates": len(orders),
            "archived": archived,
            "skipped": skipped,
            "dry_run": dry_run,
            "note": "snapshot_only_hot_rows_retained",
        }
