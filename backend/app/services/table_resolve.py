"""桌台扫码解析：优先 qr_token；开发环境兼容用桌号当 token（多店须带 store_id）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import DiningTable


async def resolve_dining_table(
    db: AsyncSession,
    token: str | None,
    *,
    store_id: int | None = None,
) -> DiningTable | None:
    raw = (token or "").strip()
    if not raw:
        return None

    result = await db.execute(
        select(DiningTable).where(DiningTable.qr_token == raw, DiningTable.status == 1)
    )
    table = result.scalar_one_or_none()
    if table:
        if store_id and int(table.store_id) != int(store_id):
            return None
        return table

    # 开发联调：可用桌号当 token；正式环境仅 qr_token
    settings = get_settings()
    if not settings.is_development:
        return None

    q = select(DiningTable).where(DiningTable.table_no == raw, DiningTable.status == 1)
    if store_id:
        q = q.where(DiningTable.store_id == int(store_id))
        return (await db.execute(q.order_by(DiningTable.id).limit(1))).scalar_one_or_none()

    # 未指定门店：多店同号则拒绝盲选，避免串店
    rows = list((await db.execute(q.order_by(DiningTable.id).limit(2))).scalars().all())
    if len(rows) != 1:
        return None
    return rows[0]
