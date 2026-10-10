"""门店隔离辅助"""
from __future__ import annotations

from app.core.exceptions import AppException
from app.models import Order, User


def effective_store_id(user: User, requested: int | None = None) -> int | None:
    """非超管强制本店；超管可用 requested 或 None(全店)。

    注意：非超管未绑定门店时返回 None，调用方应再走 require_bound_store()。
    """
    if user.role.code == "SUPER_ADMIN":
        return int(requested) if requested else None
    if user.store_id:
        return int(user.store_id)
    return None


def require_bound_store(user: User, requested: int | None = None) -> int | None:
    """解析厨房/订单等列表的门店范围。

    - 超管：requested 有值则单店，否则 None=全店
    - 非超管：必须绑定门店，忽略 requested，返回本店 id
    """
    if user.role.code == "SUPER_ADMIN":
        return int(requested) if requested else None
    if not user.store_id:
        raise AppException(40300, "账号未绑定门店", status_code=403)
    return int(user.store_id)


def assert_store_access(user: User, store_id: int | None) -> None:
    if user.role.code == "SUPER_ADMIN":
        return
    if user.store_id is None:
        raise AppException(40300, "账号未绑定门店", status_code=403)
    if store_id is None or int(store_id) != int(user.store_id):
        raise AppException(40300, "无权访问该门店数据", status_code=403)


def assert_order_store_access(user: User, order: Order) -> None:
    assert_store_access(user, order.store_id)
