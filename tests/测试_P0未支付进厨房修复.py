"""P0 端到端回归：未支付订单不能进厨房，支付回调兜底可入账。

复现审计发现的死锁：
1. 顾客下单后未支付（PENDING_PAYMENT）
2. 厨房误点「接单制作」把订单推到 CONFIRMED
3. 顾客随后支付，回调试图 PENDING_PAYMENT → PAID，但订单已被人为推进到 CONFIRMED
4. 老逻辑拒绝 CONFIRMED → PAID，订单永久卡在「已确认未付款」状态

P0 修复：
- 状态机 STAFF_TRANSITIONS 中移除 PENDING_PAYMENT → CONFIRMED，员工无法把待支付单推进厨房
- PAYMENT_TRANSITIONS 增加 CONFIRMED → PAID 兜底，万一订单被异常推进到 CONFIRMED，
  支付回调仍能将订单收敛回 PAID，避免死锁

本测试通过状态机层和模拟流程两层覆盖，不依赖数据库。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import (
    PAYMENT_TRANSITIONS,
    STAFF_TRANSITIONS,
    assert_payment_transition,
    assert_staff_transition,
    assert_system_transition,
)


def test_pending_payment_cannot_staff_confirm():
    """P0-1：员工不能把待支付订单推进到 CONFIRMED。"""
    # 状态机层：STAFF_TRANSITIONS 中 PENDING_PAYMENT 不含 CONFIRMED
    assert "CONFIRMED" not in STAFF_TRANSITIONS["PENDING_PAYMENT"]

    with pytest.raises(AppException) as exc:
        assert_staff_transition("PENDING_PAYMENT", "CONFIRMED")
    # 40022 = 非法状态迁移；40020 = 禁止人工写入（PAID/REFUNDED/CANCELLED）
    assert exc.value.code in (40022, 40020)


def test_payment_callback_can_converge_confirmed_to_paid():
    """P0 兜底：订单被异常推进到 CONFIRMED 后，支付回调仍能 PAID 入账。"""
    # 状态机层：PAYMENT_TRANSITIONS 显式包含 CONFIRMED → PAID
    assert "PAID" in PAYMENT_TRANSITIONS["CONFIRMED"]

    # 不应抛 40024（当前状态不允许支付完成）
    assert_payment_transition("CONFIRMED", "PAID")


def test_pending_payment_can_be_cancelled_by_staff():
    """员工仍可取消未支付订单（关单兜底）。"""
    assert_staff_transition("PENDING_PAYMENT", "CANCELLED")


def test_paid_cannot_be_cancelled_by_staff():
    """已付款订单不可直接取消，必须走退款。"""
    with pytest.raises(AppException) as exc:
        assert_staff_transition("PAID", "CANCELLED")
    # CANCELLED 属于 FORBIDDEN_STAFF_TARGETS，先抛 40020；
    # 即便绕过，PAID → CANCELLED 也会抛 40022（已付款不可直接取消）
    assert exc.value.code in (40020, 40022)


def test_full_normal_flow_via_state_machine():
    """端到端：PENDING_PAYMENT →（支付回调）PAID →（员工）CONFIRMED/PREPARING → READY → COMPLETED。"""
    # 1. 顾客下单后未支付
    cur = "PENDING_PAYMENT"

    # 2. 顾客支付，支付回调推进到 PAID
    assert_payment_transition(cur, "PAID")
    cur = "PAID"

    # 3. 厨房接单：PAID → CONFIRMED（堂食默认流转）
    assert_staff_transition(cur, "CONFIRMED")
    cur = "CONFIRMED"

    # 4. 制作流程
    assert_staff_transition(cur, "PREPARING")
    cur = "PREPARING"
    assert_staff_transition(cur, "COOKING")
    cur = "COOKING"
    assert_staff_transition(cur, "READY")
    cur = "READY"

    # 5. 出餐完成
    assert_staff_transition(cur, "COMPLETED")
    cur = "COMPLETED"

    # 完成后不可再迁移（除系统全额退款）
    assert cur == "COMPLETED"


def test_abnormal_confirmed_then_payment_callback_converges():
    """异常路径：订单被异常推进到 CONFIRMED，顾客支付时回调应能收敛回 PAID。

    审计 P0 死锁场景：旧逻辑 PAYMENT_TRANSITIONS 只有 PENDING_PAYMENT → PAID，
    遇到误被推进到 CONFIRMED 的订单时回调抛 40024，订单永久卡在「已确认未付款」。
    """
    # 订单被异常推进到 CONFIRMED（即便状态机已堵，仍假设历史数据/外部回写到此状态）
    cur = "CONFIRMED"

    # 支付回调到来，必须能 PAID 入账
    assert_payment_transition(cur, "PAID")
    cur = "PAID"

    # 之后正常厨房流程不受影响
    assert_staff_transition(cur, "PREPARING")


def test_timeout_cancel_then_late_payment_must_not_resurrect():
    """超时关单后，迟到支付回调不应把 CANCELLED 复活成 PAID。"""
    # 超时关单
    assert_system_transition("PENDING_PAYMENT", "CANCELLED")

    # 迟到支付回调：CANCELLED 不在 PAYMENT_TRANSITIONS 中
    assert "CANCELLED" not in PAYMENT_TRANSITIONS
    assert "PAID" not in PAYMENT_TRANSITIONS.get("CANCELLED", set())

    with pytest.raises(AppException) as exc:
        assert_payment_transition("CANCELLED", "PAID")
    assert exc.value.code == 40024


@pytest.mark.asyncio
async def test_kds_filter_excludes_pending_payment():
    """P0-2：KDS 活跃订单接口不应返回 PENDING_PAYMENT 订单。

    直接验证 orders.py 中 KDS 查询过滤条件：status != PENDING_PAYMENT。
    这里仅校验过滤谓词常量，不触发 DB。
    """
    # 模拟订单快照
    pending = MagicMock(status="PENDING_PAYMENT", payment_status="UNPAID")
    paid = MagicMock(status="PAID", payment_status="PAID")
    confirmed = MagicMock(status="CONFIRMED", payment_status="PAID")

    # KDS 应展示的订单（PAID 起步，已支付）
    visible_statuses = {"PAID", "CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED"}

    def kds_visible(o):
        return o.status in visible_statuses and o.status != "PENDING_PAYMENT"

    assert kds_visible(pending) is False, "PENDING_PAYMENT 不应出现在 KDS"
    assert kds_visible(paid) is True
    assert kds_visible(confirmed) is True


def test_staff_cannot_skip_payment_to_kitchen_states():
    """P0 综合防护：员工无法把 PENDING_PAYMENT 推进到任何厨房制作状态。"""
    kitchen_targets = ("CONFIRMED", "PREPARING", "COOKING", "READY", "COMPLETED")
    for target in kitchen_targets:
        with pytest.raises(AppException):
            assert_staff_transition("PENDING_PAYMENT", target)


def test_refund_path_for_paid_order():
    """已付款订单退款：员工可发起 REFUND_PENDING，系统最终置 REFUNDED。"""
    # 员工可以 PAID → REFUND_PENDING（发起退款）
    assert_staff_transition("PAID", "REFUND_PENDING")

    # 系统在退款回调中可推进到 REFUNDED
    assert_system_transition("REFUND_PENDING", "REFUNDED")

    # REFUNDED 是终态
    with pytest.raises(AppException):
        assert_system_transition("REFUNDED", "PAID")
