"""订单状态机：仅允许合法迁移；PAID 只能由支付回调写入。"""
from __future__ import annotations

from app.core.exceptions import AppException

# 后台/厨房可手动推进的状态迁移（不含 PAID）
STAFF_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"CANCELLED"},
    "PAID": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"PREPARING", "COOKING", "CANCELLED"},
    "PREPARING": {"COOKING", "READY", "CANCELLED"},
    "COOKING": {"READY", "CANCELLED"},
    "READY": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

# 支付成功回调允许的迁移
PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"PAID"},
}

# 外卖平台落单等系统写入
SYSTEM_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_PAYMENT": {"PAID", "CANCELLED"},
    "PAID": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"CANCELLED", "COMPLETED"},
    "PREPARING": {"CANCELLED"},
    "COOKING": {"CANCELLED"},
    "READY": {"CANCELLED", "COMPLETED"},
}

FORBIDDEN_STAFF_TARGETS = frozenset({"PAID"})


def assert_staff_transition(current: str, target: str) -> None:
    if target in FORBIDDEN_STAFF_TARGETS:
        raise AppException(40020, "已支付状态只能由支付回调写入，禁止人工改成 PAID")
    allowed = STAFF_TRANSITIONS.get(current)
    if allowed is None:
        raise AppException(40021, f"未知订单状态: {current}")
    if target not in allowed:
        raise AppException(40022, f"非法状态迁移: {current} → {target}")


def assert_payment_transition(current: str, target: str) -> None:
    if target != "PAID":
        raise AppException(40023, f"支付回调只能将订单置为 PAID，收到: {target}")
    allowed = PAYMENT_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise AppException(40024, f"当前状态不允许支付完成: {current}")


def assert_system_transition(current: str, target: str) -> None:
    allowed = SYSTEM_TRANSITIONS.get(current, set())
    if target not in allowed and current != target:
        raise AppException(40025, f"系统非法状态迁移: {current} → {target}")
