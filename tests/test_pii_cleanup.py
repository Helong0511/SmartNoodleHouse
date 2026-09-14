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
    """UPDATE 语句应包含 recipient_phone IS NOT NULL + order_id IN (子查询)。"""
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
