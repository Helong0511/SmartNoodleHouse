"""订单状态机：仅允许合法迁移；PAID 只能由支付回调写入。"""
from __future__ import annotations

from app.core.exceptions import AppException

# 后台/厨房可手动推进的状态迁移（不含 PAID / REFUNDED）
# 已付款订单禁止直接 CANCELLED：须走退款流程 → REFUNDED
# P0 修复：PENDING_PAYMENT 仅允许 CANCELLED；CONFIRMED 必须由 PAID 推进，
#   防止未支付订单进入厨房制作后回调无法入账（40024 死锁）。
STAFF_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"CANCELLED"},
    # 免烹饪单：PAID/CONFIRMED → READY（制作完成）；出餐必须 READY → COMPLETED
    "PAID": {"CONFIRMED", "PREPARING", "READY", "REFUND_PENDING"},
    "CONFIRMED": {"PREPARING", "COOKING", "READY", "REFUND_PENDING"},
    "PREPARING": {"COOKING", "READY", "REFUND_PENDING"},
    "COOKING": {"READY", "REFUND_PENDING"},
    "READY": {"COMPLETED", "REFUND_PENDING"},
    "REFUND_PENDING": set(),  # 仅退款成功回调/系统可离开
    "COMPLETED": set(),
    "CANCELLED": set(),
    "REFUNDED": set(),
}

# 支付成功回调允许的订单状态迁移。
# CANCELLED 是不可复活的业务终态：迟到成功回调只确认支付流水并通过 Outbox 原路退款，
# 不得把订单改回 PAID，也不得重新扣库存或恢复已释放的营销权益。
PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"PAID"},
    "CONFIRMED": {"PAID"},
}

# 系统/外卖/超时/退款写入（含厨房同步后的平台回写）
SYSTEM_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"PAID", "CANCELLED"},
    "PAID": {"CONFIRMED", "REFUND_PENDING", "REFUNDED", "COMPLETED", "PREPARING"},
    # PAID：外卖平台接单失败回滚
    "CONFIRMED": {"PREPARING", "COOKING", "READY", "COMPLETED", "REFUND_PENDING", "REFUNDED", "PAID"},
    "PREPARING": {"COOKING", "READY", "COMPLETED", "REFUND_PENDING", "REFUNDED"},
    "COOKING": {"READY", "COMPLETED", "REFUND_PENDING", "REFUNDED"},
    "READY": {"COMPLETED", "REFUND_PENDING", "REFUNDED"},
    "REFUND_PENDING": {"REFUNDED", "PAID", "CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED"},
    # 完成后全额退款
    "COMPLETED": {"REFUND_PENDING", "REFUNDED"},
    "CANCELLED": set(),
    "REFUNDED": set(),
}

FORBIDDEN_STAFF_TARGETS = frozenset({"PAID", "REFUNDED", "CANCELLED"})


def assert_staff_transition(current: str, target: str) -> None:
    # PENDING_PAYMENT → CANCELLED 仍允许员工关未付款单
    if target == "CANCELLED" and current == "PENDING_PAYMENT":
        return
    if target in FORBIDDEN_STAFF_TARGETS:
        raise AppException(40020, f"状态 {target} 只能由支付/系统回调写入，禁止人工直接改写")
    allowed = STAFF_TRANSITIONS.get(current)
    if allowed is None:
        raise AppException(40021, f"未知订单状态: {current}")
    if target not in allowed:
        if target == "CANCELLED" and current not in ("PENDING_PAYMENT", "CANCELLED"):
            raise AppException(
                40022,
                "已付款订单不可直接取消，请发起退款；退款成功后订单将变为已退款",
            )
        raise AppException(40022, f"非法状态迁移: {current} → {target}")


def assert_payment_transition(current: str, target: str) -> None:
    if target != "PAID":
        raise AppException(40023, f"支付回调只能将订单置为 PAID，收到: {target}")
    allowed = PAYMENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise AppException(40024, f"当前状态不允许支付完成: {current}")


def assert_system_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = SYSTEM_TRANSITIONS.get(current)
    if allowed is None:
        raise AppException(40021, f"未知订单状态: {current}")
    if target not in allowed:
        raise AppException(40025, f"系统非法状态迁移: {current} → {target}")
