"""角色权限矩阵与字段门闸一致性测试（对照 sql/data_sys.sql 种子约定）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.field_policy import FIELD_PERMISSION_GATES, apply_field_policy


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_SYS = REPO_ROOT / "sql" / "data_sys.sql"

# 与 data_sys.sql 角色矩阵对齐的期望权限（不含 SUPER_ADMIN 全量）
EXPECTED_ROLE_PERMS: dict[str, frozenset[str]] = {
    "STORE_ADMIN": frozenset(
        {
            "product:read",
            "product:create",
            "product:update",
            "product:delete",
            "order:read",
            "order:update",
            "customer:read",
            "customer:write",
            "camera:read",
            "camera:manage",
            "welcome:manage",
            "payment:read",
            "payment:refund",
            "table:manage",
            "user:read",
            "user:manage",
            "coupon:read",
            "coupon:manage",
            "audit:read",
            "wallet:read",
            "wallet:manage",
            "settings:store",
            "invoice:manage",
            "customer:pii",
            "product:cost:read",
            "role:delegate",
            "reconcile:read",
            "reconcile:manage",
            "archive:manage",
        }
    ),
    "CASHIER": frozenset(
        {
            "order:read",
            "order:update",
            "payment:read",
            "customer:read",
            "product:read",
            "wallet:read",
        }
    ),
    "KITCHEN": frozenset({"order:read", "order:update"}),
    "OPERATOR": frozenset(
        {
            "product:read",
            "product:create",
            "product:update",
            "product:delete",
            "order:read",
            "customer:read",
            "payment:read",
            "wallet:read",
        }
    ),
}

# 后端实际用到的权限码（不得出现未种子化的幽灵码）
API_PERMISSION_CODES = frozenset(
    {
        "product:read",
        "product:create",
        "product:update",
        "product:delete",
        "product:cost:read",
        "order:read",
        "order:update",
        "invoice:manage",
        "archive:manage",
        "customer:read",
        "customer:manage",
        "customer:write",
        "customer:pii",
        "camera:read",
        "camera:manage",
        "welcome:manage",
        "payment:read",
        "payment:refund",
        "reconcile:read",
        "reconcile:manage",
        "coupon:read",
        "coupon:manage",
        "wallet:read",
        "wallet:manage",
        "table:manage",
        "user:read",
        "user:manage",
        "role:delegate",
        "system:manage",
        "settings:store",
        "audit:read",
    }
)


def _extract_permission_codes(sql: str) -> set[str]:
    codes: set[str] = set()
    for line in sql.splitlines():
        s = line.strip()
        if s.startswith("SELECT '") and ":" in s and s.endswith("',"):
            # SELECT 'product:read',
            code = s[len("SELECT '") : -2]
            if ":" in code and " " not in code:
                codes.add(code)
    return codes


def test_roles_defined():
    assert set(EXPECTED_ROLE_PERMS) == {"STORE_ADMIN", "CASHIER", "KITCHEN", "OPERATOR"}


def test_data_sys_seeds_all_api_permission_codes():
    sql = DATA_SYS.read_text(encoding="utf-8")
    seeded = _extract_permission_codes(sql)
    missing = API_PERMISSION_CODES - seeded
    assert not missing, f"data_sys.sql 缺少权限码: {sorted(missing)}"
    assert "financial:read" not in seeded, "不应再种子化幽灵权限 financial:read"


def test_store_admin_matrix_in_data_sys():
    sql = DATA_SYS.read_text(encoding="utf-8")
    # STORE_ADMIN 主矩阵块应包含 customer:write，且校正段禁止 customer:manage
    assert "'customer:write'" in sql
    assert "AND `p`.`code` = 'customer:manage'" in sql
    assert "WHERE `r`.`code` = 'STORE_ADMIN'" in sql
    for code in EXPECTED_ROLE_PERMS["STORE_ADMIN"]:
        assert f"'{code}'" in sql, f"data_sys 未出现店长权限 {code}"


def test_store_admin_must_not_have_platform_only_perms():
    forbidden = {"system:manage", "customer:manage"}
    assert not (EXPECTED_ROLE_PERMS["STORE_ADMIN"] & forbidden)


def test_cashier_kitchen_operator_no_refund():
    for role in ("CASHIER", "KITCHEN", "OPERATOR"):
        assert "payment:refund" not in EXPECTED_ROLE_PERMS[role]


def test_cost_price_gate_only_product_cost_read():
    assert FIELD_PERMISSION_GATES["cost_price"] == frozenset({"product:cost:read"})
    assert "financial:read" not in FIELD_PERMISSION_GATES["cost_price"]


def test_field_policy_masks_phone_without_pii():
    payload = {"phone": "13812345678", "cost_price": "12.50", "name": "面"}
    out = apply_field_policy(payload, permissions={"product:read"}, is_super_admin=False)
    assert out["phone"] == "138****5678"
    assert "cost_price" not in out
    assert out["name"] == "面"


def test_field_policy_keeps_cost_with_product_cost_read():
    payload = {"cost_price": "9.9", "phone": "13900001111"}
    out = apply_field_policy(
        payload,
        permissions={"product:cost:read", "customer:pii"},
        is_super_admin=False,
    )
    assert out["cost_price"] == "9.9"
    assert out["phone"] == "13900001111"


@pytest.mark.asyncio
async def test_require_permission_rejects_missing_code(db_session):
    """无权限员工访问 require_permission 路径应 403。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_current_user, get_db
    from app.main import app

    role = SimpleNamespace(code="OPERATOR")
    user = SimpleNamespace(id=91001, role_id=5, role=role, store_id=1, status=1, token_version=0)

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch(
            "app.api.deps.get_user_permissions",
            AsyncMock(return_value=set(EXPECTED_ROLE_PERMS["OPERATOR"])),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # 排队管理 API 要 table:manage；运营角色没有
                res = await ac.get("/api/admin/queue/today", params={"store_id": 1})
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 403, f"运营访问排队应 403，实际 {res.status_code}: {res.text}"
