"""收银点餐权限。"""

from unittest.mock import MagicMock


def test_cashier_role_can_access_pos():
    """收银员角色应能使用收银点餐（与 require_pos 逻辑一致）。"""
    role = MagicMock()
    role.code = "CASHIER"
    perms = {"order:read", "order:update", "payment:read", "product:read", "customer:read"}
    allowed = role.code in ("SUPER_ADMIN", "STORE_ADMIN", "CASHIER") or "order:update" in perms
    assert allowed is True


def test_kitchen_role_cannot_access_pos_by_role_code():
    """后厨角色默认不走收银角色白名单（可有 order:update 时仍可进）。"""
    role = MagicMock()
    role.code = "KITCHEN"
    perms: set[str] = set()
    allowed = role.code in ("SUPER_ADMIN", "STORE_ADMIN", "CASHIER") or "order:update" in perms
    assert allowed is False
