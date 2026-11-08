from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.products_stocks import _assert_no_active_orders_for_stock_mutation
from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_stock_source_mutation_is_blocked_while_product_has_active_order():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=123)

    with pytest.raises(AppException) as exc:
        await _assert_no_active_orders_for_stock_mutation(
            db, store_id=7, product_id=42
        )

    assert exc.value.code == 40962
    statement = db.scalar.await_args.args[0]
    sql = str(statement).upper()
    assert "ORDER_ITEMS" in sql
    assert "JOIN ORDERS" in sql
    assert "STORE_ID" in sql
    assert "PRODUCT_ID" in sql
    assert "NOT IN" in sql
    params = statement.compile().params
    values = []
    for value in params.values():
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item) for item in value)
        else:
            values.append(str(value))
    assert {"CANCELLED", "REFUNDED", "COMPLETED"}.issubset(set(values))


@pytest.mark.asyncio
async def test_stock_source_mutation_is_allowed_when_no_active_order_references_product():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)

    await _assert_no_active_orders_for_stock_mutation(
        db, store_id=7, product_id=42
    )
    db.scalar.assert_awaited_once()


def test_both_stock_override_mutation_routes_apply_active_order_guard():
    from pathlib import Path

    import ast

    source = (
        Path(__file__).resolve().parents[1] / "backend/app/api/products_stocks.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded_routes = {"upsert_store_stock", "delete_store_stock"}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in guarded_routes:
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "_assert_no_active_orders_for_stock_mutation"
                for child in ast.walk(node)
            ):
                found.add(node.name)
    assert found == guarded_routes
