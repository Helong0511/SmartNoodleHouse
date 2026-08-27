"""门店隔离与下单 store_id 约束回归"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.store_scope import assert_order_store_access, assert_store_access, effective_store_id


def _user(role: str, store_id: int | None):
    return SimpleNamespace(store_id=store_id, role=SimpleNamespace(code=role))


def test_effective_store_forces_staff_store():
    u = _user("CASHIER", 3)
    assert effective_store_id(u, requested=99) == 3
    assert effective_store_id(_user("SUPER_ADMIN", None), requested=99) == 99


def test_assert_store_access_blocks_cross_store():
    u = _user("MANAGER", 2)
    with pytest.raises(HTTPException) as ei:
        assert_store_access(u, 9)
    assert ei.value.status_code == 403
    assert_store_access(u, 2)
    assert_store_access(_user("SUPER_ADMIN", None), 9)


def test_assert_order_store_access():
    order = SimpleNamespace(store_id=5)
    with pytest.raises(HTTPException):
        assert_order_store_access(_user("WAITER", 1), order)
    assert_order_store_access(_user("WAITER", 5), order)
