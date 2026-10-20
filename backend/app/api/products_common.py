"""餐品路由共用 helper（菜品 CRUD / 规格组复用）。"""
from __future__ import annotations

from sqlalchemy import select

from app.api.deps import DbSession
from app.core.exceptions import AppException
from app.models import OptionGroup, Store


async def store_code_prefix(db: DbSession, store_id: int | None) -> str:
    from app.services.option_code import HQ_STORE_PREFIX, normalize_store_prefix

    if store_id is None:
        return HQ_STORE_PREFIX
    store = await db.get(Store, int(store_id))
    return normalize_store_prefix(getattr(store, "store_no", None) if store else None)


async def assert_groups_usable_for_store(db: DbSession, group_ids: list[int], store_id: int | None) -> None:
    """餐品只能绑总部共享或本店规格组。"""
    if not group_ids:
        return
    rows = list(
        (
            await db.execute(
                select(OptionGroup).where(OptionGroup.id.in_(group_ids), OptionGroup.status == 1)
            )
        ).scalars().all()
    )
    found = {int(g.id): g for g in rows}
    missing = [gid for gid in group_ids if int(gid) not in found]
    if missing:
        raise AppException(40003, f"规格组不存在或已禁用: {missing}")
    if store_id is None:
        return
    sid = int(store_id)
    bad = [
        g.id
        for g in rows
        if g.store_id is not None and int(g.store_id) != sid
    ]
    if bad:
        raise AppException(40003, f"规格组不属于本店或总部共享: {bad}")

