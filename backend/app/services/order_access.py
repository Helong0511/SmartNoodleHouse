"""订单访问控制：顾客仅能访问自己的订单；未绑定 POS 单仅员工可访问。"""
from __future__ import annotations

from app.core.exceptions import AppException
from app.models import Order


def assert_customer_owns_order(order: Order, customer_id: int, *, action: str = "访问") -> None:
    """顾客访问订单的统一守卫。

    - 未绑定顾客（典型 POS 堂食单）：一律拒绝，禁止 claim
    - 已绑定但非本人：拒绝
    """
    if order.customer_id is None:
        ch = (order.channel or "").strip().upper()
        if ch == "POS" or not ch:
            raise AppException(40300, f"无权{action}未绑定顾客的门店订单", status_code=403)
        raise AppException(40300, f"无权{action}此订单", status_code=403)
    if int(order.customer_id) != int(customer_id):
        raise AppException(40300, f"无权{action}此订单", status_code=403)


async def assert_dining_session_owner(
    db, order: Order, customer_id: int, *, action: str = "访问"
) -> None:
    """P2-3 深度校验：若订单关联了 dining_session，session 的 customer_id 必须等于当前顾客。

    用于防止数据不一致（如会话被错误挂到他人订单）导致的越权访问。
    """
    from app.models import DiningSession

    sess_id = getattr(order, "dining_session_id", None)
    if not sess_id:
        return
    sess = await db.get(DiningSession, sess_id)
    if sess is not None and int(sess.customer_id) != int(customer_id):
        raise AppException(40300, f"无权{action}此订单", status_code=403)


def assert_customer_can_pay_order(order: Order, customer_id: int) -> None:
    assert_customer_owns_order(order, customer_id, action="支付")
