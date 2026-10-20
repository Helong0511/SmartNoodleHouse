"""订单路由共用 helper（列表/厨房/叫号屏复用）。"""
from __future__ import annotations

from sqlalchemy import select

from app.models import DiningTable, Store


def table_payload(table: DiningTable | None) -> dict | None:
    if not table:
        return None
    return {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
    }


async def load_tables(db, table_ids: list[int]) -> dict[int, DiningTable]:
    ids = [i for i in table_ids if i]
    if not ids:
        return {}
    rows = await db.execute(select(DiningTable).where(DiningTable.id.in_(ids)))
    return {t.id: t for t in rows.scalars().all()}


async def load_stores(db, store_ids: list[int]) -> dict[int, Store]:
    ids = list({i for i in store_ids if i})
    if not ids:
        return {}
    rows = await db.execute(select(Store).where(Store.id.in_(ids)))
    return {s.id: s for s in rows.scalars().all()}
