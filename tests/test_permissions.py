"""权限测试"""
import pytest


ROLES = ["SUPER_ADMIN", "STORE_ADMIN", "CASHIER", "KITCHEN", "OPERATOR"]
PERMISSIONS = [
    "product:read", "product:create", "product:update", "product:delete",
    "order:read", "order:update", "customer:read",
    "camera:read", "camera:manage", "welcome:manage",
    "payment:read", "payment:refund", "system:manage",
]


def test_roles_defined():
    assert "SUPER_ADMIN" in ROLES
    assert "KITCHEN" in ROLES


def test_permissions_defined():
    assert "payment:refund" in PERMISSIONS
    assert "camera:manage" in PERMISSIONS
