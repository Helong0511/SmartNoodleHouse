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


def _service_user_result(users):
    result = MagicMock()
    result.scalars.return_value.all.return_value = users
    return result


@pytest.mark.asyncio
async def test_delegation_service_rejects_permission_not_directly_owned():
    from datetime import timedelta

    from app.core.response import now_local
    from app.services.role_delegation_service import RoleDelegationService

    grantor = SimpleNamespace(id=1, role_id=10, store_id=7, status=1)
    delegatee = SimpleNamespace(id=2, role_id=20, store_id=7, status=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _service_user_result([grantor, delegatee]),
        _permission_result(["order:read"]),
    ])
    db.get = AsyncMock(return_value=SimpleNamespace(code="STORE_ADMIN"))

    with pytest.raises(ValueError, match="直接持有"):
        await RoleDelegationService.create(
            db,
            grantor_user_id=1,
            delegatee_user_id=2,
            permission_codes=["payment:refund"],
            starts_at=now_local(),
            ends_at=now_local() + timedelta(hours=1),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_delegation_service_rejects_cross_store_target():
    from datetime import timedelta

    from app.core.response import now_local
    from app.services.role_delegation_service import RoleDelegationService

    grantor = SimpleNamespace(id=1, role_id=10, store_id=7, status=1)
    delegatee = SimpleNamespace(id=2, role_id=20, store_id=8, status=1)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_service_user_result([grantor, delegatee]))
    db.get = AsyncMock(return_value=SimpleNamespace(code="STORE_ADMIN"))

    with pytest.raises(ValueError, match="本店"):
        await RoleDelegationService.create(
            db,
            grantor_user_id=1,
            delegatee_user_id=2,
            permission_codes=["order:read"],
            starts_at=now_local(),
            ends_at=now_local() + timedelta(hours=1),
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_delegation_service_rejects_disabled_delegatee():
    from datetime import timedelta

    from app.core.response import now_local
    from app.services.role_delegation_service import RoleDelegationService

    grantor = SimpleNamespace(id=1, role_id=10, store_id=7, status=1)
    delegatee = SimpleNamespace(id=2, role_id=20, store_id=7, status=0)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_service_user_result([grantor, delegatee]))

    with pytest.raises(ValueError, match="已停用"):
        await RoleDelegationService.create(
            db,
            grantor_user_id=1,
            delegatee_user_id=2,
            permission_codes=["order:read"],
            starts_at=now_local(),
            ends_at=now_local() + timedelta(hours=1),
        )

    db.get.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_delegation_service_allows_only_direct_role_permissions():
    from datetime import timedelta

    from app.core.response import now_local
    from app.services.role_delegation_service import RoleDelegationService

    grantor = SimpleNamespace(id=1, role_id=10, store_id=7, status=1)
    delegatee = SimpleNamespace(id=2, role_id=20, store_id=7, status=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _service_user_result([grantor, delegatee]),
        _permission_result(["order:read", "order:update"]),
    ])
    db.get = AsyncMock(return_value=SimpleNamespace(code="STORE_ADMIN"))
    db.add = MagicMock()
    db.flush = AsyncMock()

    row = await RoleDelegationService.create(
        db,
        grantor_user_id=1,
        delegatee_user_id=2,
        permission_codes=["order:read"],
        starts_at=now_local(),
        ends_at=now_local() + timedelta(hours=1),
    )

    assert row.permission_codes == ["order:read"]
    assert row.status == "ACTIVE"
    db.add.assert_called_once_with(row)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegation_service_never_grants_system_manage_even_for_super_admin():
    from datetime import timedelta

    from app.core.response import now_local
    from app.services.role_delegation_service import RoleDelegationService

    with pytest.raises(ValueError, match="system:manage"):
        await RoleDelegationService.create(
            MagicMock(),
            grantor_user_id=1,
            delegatee_user_id=2,
            permission_codes=["system:manage"],
            starts_at=now_local(),
            ends_at=now_local() + timedelta(hours=1),
        )
