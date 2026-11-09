"""Shared-cart checkout must use the current locked revision and exact cart snapshot."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import AppException
from app.services.shared_cart_service import SharedCartService


def _db(rows):
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    return SimpleNamespace(execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_checkout_accepts_exact_shared_cart_snapshot(monkeypatch):
    party = SimpleNamespace(id=4, revision=9)
    monkeypatch.setattr(SharedCartService, "get_open_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_lock_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_require_member", AsyncMock(return_value=SimpleNamespace()))
    row = SimpleNamespace(product_id=10, quantity=2, option_ids=[7, 3], remark="少辣")

    await SharedCartService.validate_checkout(
        _db([row]),
        store_id=1,
        table_id=2,
        customer_id=3,
        party_id=4,
        expected_revision=9,
        items=[{"product_id": 10, "quantity": 2, "option_ids": [3, 7], "remark": "少辣"}],
    )


@pytest.mark.asyncio
async def test_checkout_rejects_stale_revision(monkeypatch):
    party = SimpleNamespace(id=4, revision=10)
    monkeypatch.setattr(SharedCartService, "get_open_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_lock_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_require_member", AsyncMock(return_value=SimpleNamespace()))

    with pytest.raises(AppException) as exc:
        await SharedCartService.validate_checkout(
            _db([]),
            store_id=1,
            table_id=2,
            customer_id=3,
            party_id=4,
            expected_revision=9,
            items=[],
        )
    assert exc.value.code == 40920


@pytest.mark.asyncio
async def test_checkout_rejects_client_cart_different_from_shared_snapshot(monkeypatch):
    party = SimpleNamespace(id=4, revision=9)
    monkeypatch.setattr(SharedCartService, "get_open_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_lock_party", AsyncMock(return_value=party))
    monkeypatch.setattr(SharedCartService, "_require_member", AsyncMock(return_value=SimpleNamespace()))
    row = SimpleNamespace(product_id=10, quantity=2, option_ids=[7], remark="少辣")

    with pytest.raises(AppException) as exc:
        await SharedCartService.validate_checkout(
            _db([row]),
            store_id=1,
            table_id=2,
            customer_id=3,
            party_id=4,
            expected_revision=9,
            items=[{"product_id": 10, "quantity": 1, "option_ids": [7], "remark": "少辣"}],
        )
    assert exc.value.code == 40921
