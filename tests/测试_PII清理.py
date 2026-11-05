"""P1: PII 生命周期清理 — 外卖订单超期 PII 脱敏。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pii_cleanup_service import (
    DEFAULT_PII_RETENTION_DAYS,
    anonymize_expired_delivery_pii,
)


@pytest.mark.asyncio
async def test_anonymize_issues_update_with_correct_filters():
    """订单、退款、回调日志的原始 PII payload 均按同一截止时间清理。"""
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 3
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    n = await anonymize_expired_delivery_pii(db, retention_days=30)
    assert n == 3
    assert db.commit.await_count == 1

    stmt = db.execute.await_args[0][0]
    stmt_str = str(stmt).lower()
    assert "delivery_orders" in stmt_str
    assert "recipient_phone" in stmt_str
    assert "is not null" in stmt_str
    assert "recipient_name" in stmt_str
    assert "rider_phone" in stmt_str
    # 关键回归：清理子查询必须按完成/取消时间与 cutoff 比较，不能只判断时间非空。
    assert "completed_at" in stmt_str
    assert "cancelled_at" in stmt_str
    assert " < " in stmt_str
    assert "raw_payload" in stmt_str
    assert db.execute.await_count == 3
    refund_stmt = db.execute.await_args_list[1].args[0]
    callback_stmt = db.execute.await_args_list[2].args[0]
    assert "delivery_refunds" in str(refund_stmt)
    assert "raw_payload" in str(refund_stmt)
    assert "delivery_callback_logs" in str(callback_stmt)
    assert "payload" in str(callback_stmt)


@pytest.mark.asyncio
async def test_anonymize_zero_when_nothing_to_clean():
    db = MagicMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    n = await anonymize_expired_delivery_pii(db)
    assert n == 0


def test_default_retention_is_30_days():
    assert DEFAULT_PII_RETENTION_DAYS == 30
