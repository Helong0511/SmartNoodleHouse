"""Order idempotency replay must match the original business intent."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import AppException
from app.services.order_create_service import OrderCreateService


def _db_for_items(items):
    results = [
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]
    return SimpleNamespace(execute=AsyncMock(side_effect=results))


@pytest.mark.asyncio
async def test_replay_rejects_changed_product_quantity_or_remark():
    existing = [SimpleNamespace(id=10, product_id=1, quantity=2, remark="少辣")]
    db = _db_for_items(existing)

    with pytest.raises(AppException) as exc:
        await OrderCreateService._assert_idempotent_order_items_match(
            db,
            SimpleNamespace(id=9),
            [{"product_id": 2, "quantity": 2, "remark": "少辣", "option_ids": []}],
        )

    assert exc.value.code == 40902


@pytest.mark.asyncio
async def test_replay_is_independent_of_cart_line_order():
    existing = [
        SimpleNamespace(id=10, product_id=1, quantity=2, remark="少辣"),
        SimpleNamespace(id=11, product_id=2, quantity=1, remark=""),
    ]
    db = _db_for_items(existing)

    await OrderCreateService._assert_idempotent_order_items_match(
        db,
        SimpleNamespace(id=9),
        [
            {"product_id": 2, "quantity": 1, "remark": "", "option_ids": []},
            {"product_id": 1, "quantity": 2, "remark": "少辣", "option_ids": []},
        ],
    )
