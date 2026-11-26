"""RBAC 权限上限回归测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.users import _assert_can_assign_role
from app.core.exceptions import AppException


def _permission_result(codes):
    result = MagicMock()
    result.scalars.return_value.all.return_value = codes
    return result


@pytest.mark.asyncio
async def test_role_assignment_rejects_privilege_escalation():
    actor = SimpleNamespace(id=1, role_id=10, role=SimpleNamespace(code="STORE_ADMIN"))
    target_role = SimpleNamespace(id=20, code="CASHIER")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _permission_result(["order:read", "order:update"]),
        _permission_result(["order:read", "order:update", "payment:refund"]),
    ])

    with pytest.raises(AppException) as exc:
        await _assert_can_assign_role(db, actor, target_role)

    assert exc.value.code == 40310


@pytest.mark.asyncio
async def test_role_assignment_allows_permission_subset():
    actor = SimpleNamespace(id=1, role_id=10, role=SimpleNamespace(code="STORE_ADMIN"))
    target_role = SimpleNamespace(id=20, code="CASHIER")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _permission_result(["order:read", "order:update", "customer:read"]),
        _permission_result(["order:read", "order:update"]),
    ])

    await _assert_can_assign_role(db, actor, target_role)


@pytest.mark.asyncio
async def test_super_admin_can_assign_any_role_without_permission_lookup():
    actor = SimpleNamespace(id=1, role_id=1, role=SimpleNamespace(code="SUPER_ADMIN"))
    target_role = SimpleNamespace(id=20, code="CASHIER")
    db = MagicMock()
    db.execute = AsyncMock()

    await _assert_can_assign_role(db, actor, target_role)

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_manage_cannot_be_granted_by_temporary_delegation(monkeypatch):
    from app.api import deps

    checker = deps.require_permission("system:manage").dependency
    monkeypatch.setattr(
        deps,
        "get_user_permissions",
        AsyncMock(return_value={"system:manage"}),
    )
    actor = SimpleNamespace(id=7, role_id=3, role=SimpleNamespace(code="STORE_ADMIN"))

    with pytest.raises(AppException) as exc:
        await checker(actor, MagicMock())

    assert exc.value.code == 40300
