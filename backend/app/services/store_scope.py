"""门店隔离辅助"""
from __future__ import annotations

from fastapi import HTTPException

from app.models import Order, User


def effective_store_id(user: User, requested: int | None = None) -> int | None:
    """非超管强制本店；超管可用 requested 或 None(全店)。"""
    if user.role.code == "SUPER_ADMIN":
        return requested
    if user.store_id:
        return int(user.store_id)
    return requested


def assert_store_access(user: User, store_id: int | None) -> None:
    if user.role.code == "SUPER_ADMIN":
        return
    if user.store_id is None:
        raise HTTPException(status_code=403, detail="账号未绑定门店")
    if store_id is None or int(store_id) != int(user.store_id):
        raise HTTPException(status_code=403, detail="无权访问该门店数据")


def assert_order_store_access(user: User, order: Order) -> None:
    assert_store_access(user, order.store_id)
