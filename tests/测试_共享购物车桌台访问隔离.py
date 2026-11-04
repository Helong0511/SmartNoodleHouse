from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.miniprogram_party import _resolve_table_for_shared_cart
from app.core.exceptions import AppException


def _table(table_id=10, store_id=2):
    return SimpleNamespace(
        id=table_id,
        store_id=store_id,
        status=1,
        table_no=f"A{table_id}",
        table_name=f"A{table_id}",
    )


@pytest.mark.asyncio
async def test_table_id_alone_cannot_join_without_active_dining_session(monkeypatch):
    monkeypatch.setattr(
        "app.api.miniprogram_party.DiningSessionService.get_active",
        AsyncMock(return_value=None),
    )
    db = MagicMock()
    db.get = AsyncMock()
    customer = SimpleNamespace(id=7)

    with pytest.raises(AppException, match="先扫描"):
        await _resolve_table_for_shared_cart(db, customer, table_id=10)

    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_table_id_requires_active_session_for_exact_same_table(monkeypatch):
    monkeypatch.setattr(
        "app.api.miniprogram_party.DiningSessionService.get_active",
        AsyncMock(return_value=SimpleNamespace(table_id=11, store_id=2)),
    )
    db = MagicMock()
    db.get = AsyncMock()
    customer = SimpleNamespace(id=7)

    with pytest.raises(AppException, match="先扫描"):
        await _resolve_table_for_shared_cart(db, customer, table_id=10)

    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_table_id_accepts_matching_active_session_and_store(monkeypatch):
    table = _table(10, 2)
    monkeypatch.setattr(
        "app.api.miniprogram_party.DiningSessionService.get_active",
        AsyncMock(return_value=SimpleNamespace(table_id=10, store_id=2)),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=table)
    customer = SimpleNamespace(id=7)

    result = await _resolve_table_for_shared_cart(db, customer, table_id=10)

    assert result is table
    db.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_table_id_rejects_active_session_store_mismatch(monkeypatch):
    table = _table(10, 2)
    monkeypatch.setattr(
        "app.api.miniprogram_party.DiningSessionService.get_active",
        AsyncMock(return_value=SimpleNamespace(table_id=10, store_id=3)),
    )
    db = MagicMock()
    db.get = AsyncMock(return_value=table)
    customer = SimpleNamespace(id=7)

    with pytest.raises(AppException, match="门店不匹配"):
        await _resolve_table_for_shared_cart(db, customer, table_id=10)


@pytest.mark.asyncio
async def test_table_token_and_table_id_must_match(monkeypatch):
    table = _table(10, 2)
    monkeypatch.setattr(
        "app.services.table_resolve.resolve_dining_table",
        AsyncMock(return_value=table),
    )
    customer = SimpleNamespace(id=7)
    db = MagicMock()

    with pytest.raises(AppException, match="凭证与桌台编号"):
        await _resolve_table_for_shared_cart(
            db, customer, table_token="valid-qr-token", table_id=11
        )


@pytest.mark.asyncio
async def test_table_token_remains_valid_bearer_for_qr_entry(monkeypatch):
    table = _table(10, 2)
    monkeypatch.setattr(
        "app.services.table_resolve.resolve_dining_table",
        AsyncMock(return_value=table),
    )
    customer = SimpleNamespace(id=7)
    db = MagicMock()

    result = await _resolve_table_for_shared_cart(
        db, customer, table_token="valid-qr-token"
    )

    assert result is table
