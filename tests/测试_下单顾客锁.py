"""Customer merge fence for order creation."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import mysql

from app.core.exceptions import AppException
from app.models import Customer
from app.services.order_create_service import OrderCreateService


@pytest.mark.asyncio
async def test_order_customer_fence_locks_active_customer():
    customer = MagicMock(spec=Customer)
    customer.id = 123
    customer.status = 1
    result = MagicMock()
    result.scalar_one_or_none.return_value = customer
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    locked = await OrderCreateService._lock_active_customer_for_order(db, 123)

    assert locked is customer
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=mysql.dialect())).upper()
    assert "CUSTOMERS.ID =" in sql
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_order_customer_fence_rejects_merged_customer():
    customer = MagicMock(spec=Customer)
    customer.id = 123
    customer.status = 0
    result = MagicMock()
    result.scalar_one_or_none.return_value = customer
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(AppException) as exc:
        await OrderCreateService._lock_active_customer_for_order(db, 123)

    assert exc.value.code == 40102


@pytest.mark.asyncio
async def test_order_customer_fence_skips_guest_order():
    db = MagicMock()
    db.execute = AsyncMock()

    assert await OrderCreateService._lock_active_customer_for_order(db, None) is None
    db.execute.assert_not_awaited()
