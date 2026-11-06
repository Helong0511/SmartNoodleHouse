from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import AppException
from app.api import social_benefits


@pytest.mark.asyncio
async def test_anonymous_cannot_probe_arbitrary_customer_social_eligibility():
    with pytest.raises(AppException, match="请先登录"):
        await social_benefits._resolve_preview_scope(
            db=object(),
            customer=None,
            user=None,
            requested_customer_id=123,
            requested_store_id=1,
        )


@pytest.mark.asyncio
async def test_customer_preview_is_always_bound_to_own_customer_id(monkeypatch):
    monkeypatch.setattr(
        social_benefits,
        "get_user_permissions",
        AsyncMock(return_value=set()),
    )
    customer = SimpleNamespace(id=42)

    customer_id, store_id = await social_benefits._resolve_preview_scope(
        db=object(),
        customer=customer,
        user=None,
        requested_customer_id=123,
        requested_store_id=7,
    )

    assert customer_id == 42
    assert store_id == 7


@pytest.mark.asyncio
async def test_pos_staff_can_preview_only_with_pos_permission_and_bound_store(monkeypatch):
    monkeypatch.setattr(
        social_benefits,
        "get_user_permissions",
        AsyncMock(return_value=set()),
    )
    user = SimpleNamespace(
        id=9,
        role_id=3,
        role=SimpleNamespace(code="CASHIER"),
        store_id=7,
    )

    customer_id, store_id = await social_benefits._resolve_preview_scope(
        db=object(),
        customer=None,
        user=user,
        requested_customer_id=42,
        requested_store_id=7,
    )

    assert customer_id == 42
    assert store_id == 7
    social_benefits.get_user_permissions.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_pos_staff_cannot_preview_customer_social_eligibility(monkeypatch):
    monkeypatch.setattr(
        social_benefits,
        "get_user_permissions",
        AsyncMock(return_value={"customer:read"}),
    )
    user = SimpleNamespace(
        id=9,
        role_id=3,
        role=SimpleNamespace(code="REPORT_VIEWER"),
        store_id=7,
    )

    with pytest.raises(AppException, match="无权使用收银公益预览"):
        await social_benefits._resolve_preview_scope(
            db=object(),
            customer=None,
            user=user,
            requested_customer_id=42,
            requested_store_id=7,
        )


@pytest.mark.asyncio
async def test_pos_staff_cannot_probe_customer_at_another_store(monkeypatch):
    monkeypatch.setattr(
        social_benefits,
        "get_user_permissions",
        AsyncMock(return_value=set()),
    )
    user = SimpleNamespace(
        id=9,
        role_id=3,
        role=SimpleNamespace(code="CASHIER"),
        store_id=7,
    )

    with pytest.raises(AppException, match="无权访问该门店"):
        await social_benefits._resolve_preview_scope(
            db=object(),
            customer=None,
            user=user,
            requested_customer_id=42,
            requested_store_id=8,
        )


@pytest.mark.asyncio
async def test_super_admin_must_select_a_store_for_pos_preview(monkeypatch):
    monkeypatch.setattr(
        social_benefits,
        "get_user_permissions",
        AsyncMock(return_value=set()),
    )
    user = SimpleNamespace(
        id=1,
        role_id=1,
        role=SimpleNamespace(code="SUPER_ADMIN"),
        store_id=None,
    )

    customer_id, store_id = await social_benefits._resolve_preview_scope(
        db=object(),
        customer=None,
        user=user,
        requested_customer_id=42,
        requested_store_id=8,
    )

    assert customer_id == 42
    assert store_id == 8
