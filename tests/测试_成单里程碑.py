"""成单里程碑发券：校验、计单、支付后自动发放。"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.coupon_service import CouponService


def test_milestone_payload_with_template():
    row = SimpleNamespace(
        id=1,
        store_id=3,
        threshold_orders=20,
        template_id=9,
        status=1,
        created_at=None,
        updated_at=None,
    )
    tmpl = SimpleNamespace(
        name="满20单免5元",
        coupon_type="FIXED",
        discount_value=Decimal("5"),
        min_order_amount=Decimal("0"),
    )
    payload = CouponService.milestone_payload(row, tmpl)
    assert payload["id"] == 1
    assert payload["store_id"] == 3
    assert payload["threshold_orders"] == 20
    assert payload["template_id"] == 9
    assert payload["template_name"] == "满20单免5元"
    assert payload["coupon_type"] == "FIXED"
    assert "减¥5" in payload["template_summary"]


@pytest.mark.asyncio
async def test_replace_milestone_rejects_duplicate_threshold():
    db = AsyncMock()
    with pytest.raises(AppException) as ei:
        await CouponService.replace_milestone_rewards(
            db,
            1,
            [
                {"threshold_orders": 20, "template_id": 1, "status": 1},
                {"threshold_orders": 20, "template_id": 2, "status": 1},
            ],
        )
    assert ei.value.code == 40076
    assert "重复" in ei.value.message


@pytest.mark.asyncio
async def test_replace_milestone_rejects_invalid_threshold():
    db = AsyncMock()
    with pytest.raises(AppException) as ei:
        await CouponService.replace_milestone_rewards(
            db,
            1,
            [{"threshold_orders": 0, "template_id": 1, "status": 1}],
        )
    assert ei.value.code == 40076


@pytest.mark.asyncio
async def test_replace_milestone_rejects_missing_template():
    db = AsyncMock()
    with pytest.raises(AppException) as ei:
        await CouponService.replace_milestone_rewards(
            db,
            1,
            [{"threshold_orders": 20, "template_id": 0, "status": 1}],
        )
    assert ei.value.code == 40076


@pytest.mark.asyncio
async def test_replace_milestone_empty_clears_store():
    """空 items 删除本店全部规则。"""
    existing = SimpleNamespace(id=5, store_id=1)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [existing]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    with patch.object(CouponService, "list_milestone_rewards", new_callable=AsyncMock) as listed:
        listed.return_value = []
        out = await CouponService.replace_milestone_rewards(db, 1, [])
    assert out == []
    db.delete.assert_awaited_once_with(existing)
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_count_paid_orders_at_store_uses_scalar():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=42)
    n = await CouponService.count_paid_orders_at_store(db, customer_id=7, store_id=3)
    assert n == 42
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_issue_milestones_skips_without_customer():
    order = SimpleNamespace(customer_id=None, store_id=1)
    db = AsyncMock()
    out = await CouponService.issue_milestones_after_paid(db, order)
    assert out == []


@pytest.mark.asyncio
async def test_issue_milestones_skips_without_store():
    order = SimpleNamespace(customer_id=9, store_id=None)
    db = AsyncMock()
    out = await CouponService.issue_milestones_after_paid(db, order)
    assert out == []


@pytest.mark.asyncio
async def test_issue_milestones_issues_reached_thresholds():
    order = SimpleNamespace(customer_id=9, store_id=3)
    rule20 = SimpleNamespace(threshold_orders=20, template_id=101)
    rule50 = SimpleNamespace(threshold_orders=50, template_id=102)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [rule20, rule50]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)

    issued_a = [SimpleNamespace(id=1)]
    issued_b = [SimpleNamespace(id=2)]

    with patch.object(CouponService, "count_paid_orders_at_store", new_callable=AsyncMock) as cnt, patch.object(
        CouponService, "issue_by_template_id", new_callable=AsyncMock
    ) as issue:
        cnt.return_value = 55
        issue.side_effect = [issued_a, issued_b]
        out = await CouponService.issue_milestones_after_paid(db, order)

    assert out == issued_a + issued_b
    assert issue.await_count == 2
    keys = [c.kwargs.get("client_request_id") for c in issue.await_args_list]
    assert keys == ["MILESTONE:9:3:20", "MILESTONE:9:3:50"]
    assert all(c.kwargs.get("issued_at_store_id") == 3 for c in issue.await_args_list)


@pytest.mark.asyncio
async def test_issue_milestones_continues_when_quota_fails():
    """某一档配额不足时跳过该档，继续处理后续档位。"""
    order = SimpleNamespace(customer_id=9, store_id=3)
    rule20 = SimpleNamespace(threshold_orders=20, template_id=101)
    rule50 = SimpleNamespace(threshold_orders=50, template_id=102)
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [rule20, rule50]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)

    issued_b = [SimpleNamespace(id=2)]

    with patch.object(CouponService, "count_paid_orders_at_store", new_callable=AsyncMock) as cnt, patch.object(
        CouponService, "issue_by_template_id", new_callable=AsyncMock
    ) as issue:
        cnt.return_value = 60
        issue.side_effect = [AppException(40070, "优惠券库存不足"), issued_b]
        out = await CouponService.issue_milestones_after_paid(db, order)

    assert out == issued_b
    assert issue.await_count == 2


def test_parse_milestone_threshold():
    assert CouponService.parse_milestone_threshold("MILESTONE:9:3:20") == 20
    assert CouponService.parse_milestone_threshold("MILESTONE:9:3:50:REVOKED:88") == 50
    assert CouponService.parse_milestone_threshold("WELCOME:1:2") is None
    assert CouponService.parse_milestone_threshold(None) is None


@pytest.mark.asyncio
async def test_reclaim_milestones_revokes_available_above_count():
    order = SimpleNamespace(
        id=100,
        customer_id=9,
        store_id=3,
        payment_status="REFUNDED",
        status="REFUNDED",
    )
    keep = SimpleNamespace(
        id=1,
        status="AVAILABLE",
        status_reason=None,
        client_request_id="MILESTONE:9:3:20",
    )
    drop = SimpleNamespace(
        id=2,
        status="AVAILABLE",
        status_reason=None,
        client_request_id="MILESTONE:9:3:50",
    )
    used = SimpleNamespace(
        id=3,
        status="USED",
        status_reason=None,
        client_request_id="MILESTONE:9:3:100",
    )
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [keep, drop, used]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)
    db.flush = AsyncMock()

    with patch.object(CouponService, "count_paid_orders_at_store", new_callable=AsyncMock) as cnt:
        cnt.return_value = 25  # 仍达 20，未达 50/100
        revoked = await CouponService.reclaim_milestones_after_full_refund(
            db, order, refund_id=77
        )

    assert revoked == [2]
    assert drop.status == "EXPIRED"
    assert drop.status_reason == "milestone_reclaim"
    assert drop.client_request_id.endswith(":REVOKED:77")
    assert keep.status == "AVAILABLE"
    assert used.status == "USED"


@pytest.mark.asyncio
async def test_reclaim_milestones_noop_when_not_refunded():
    order = SimpleNamespace(
        id=100,
        customer_id=9,
        store_id=3,
        payment_status="PAID",
        status="PAID",
    )
    db = AsyncMock()
    out = await CouponService.reclaim_milestones_after_full_refund(db, order, refund_id=1)
    assert out == []


@pytest.mark.asyncio
async def test_replace_milestone_rejects_disabled_template():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(id=1, status=0, name="停用券", store_id=None))
    with pytest.raises(AppException) as ei:
        await CouponService.replace_milestone_rewards(
            db,
            1,
            [{"threshold_orders": 20, "template_id": 1, "status": 1}],
        )
    assert ei.value.code == 40407
