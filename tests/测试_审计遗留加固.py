"""审计清单可编码项：限流 fail-closed、字段策略、对账解析、归档、委派。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.response import now_local
from app.services.field_policy import apply_field_policy, mask_phone
from app.services.payment_reconcile_service import parse_channel_bill_csv
from app.services.role_delegation_service import RoleDelegationService


def test_mask_phone():
    assert mask_phone("13812345678") == "138****5678"


def test_field_policy_strips_cost_without_perm():
    payload = {"name": "面", "cost_price": "3.00", "phone": "13812345678"}
    apply_field_policy(payload, permissions={"product:read"})
    assert "cost_price" not in payload
    assert payload["phone"] == "138****5678"


def test_field_policy_keeps_cost_with_perm():
    payload = {"cost_price": "3.00"}
    apply_field_policy(payload, permissions={"product:cost:read"})
    assert payload["cost_price"] == "3.00"


def test_parse_channel_bill_csv():
    csv_text = "trade_no,amount,payment_no\nTX1,10.50,PAY1\nTX2,2.00,\n"
    rows = parse_channel_bill_csv("WECHAT", csv_text)
    assert len(rows) == 2
    assert rows[0]["channel_trade_no"] == "TX1"
    assert rows[0]["channel_amount"] == Decimal("10.50")


@pytest.mark.asyncio
async def test_delegation_create_and_active_codes(db_session):
    from sqlalchemy import select

    from app.models import Permission, Role, RolePermission, Store, User

    suffix = uuid.uuid4().hex[:12]
    store = Store(store_no=f"TEST-{suffix}", name="委派测试门店")
    role = Role(code=f"TMP_ROLE_{suffix}", name="临时")
    db_session.add_all([store, role])
    await db_session.flush()

    permissions = {}
    for code in ("order:update", "payment:read"):
        permission = (
            await db_session.execute(select(Permission).where(Permission.code == code))
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(code=code, name=code, module="test")
            db_session.add(permission)
            await db_session.flush()
        permissions[code] = permission
        db_session.add(RolePermission(role_id=role.id, permission_id=permission.id))

    g = User(
        username=f"grantor_{suffix}",
        password_hash="x",
        display_name="G",
        role_id=role.id,
        store_id=store.id,
        status=1,
    )
    d = User(
        username=f"delegatee_{suffix}",
        password_hash="x",
        display_name="D",
        role_id=role.id,
        store_id=store.id,
        status=1,
    )
    db_session.add_all([g, d])
    await db_session.flush()
    starts = now_local() - timedelta(minutes=1)
    ends = now_local() + timedelta(hours=2)
    await RoleDelegationService.create(
        db_session,
        grantor_user_id=g.id,
        delegatee_user_id=d.id,
        permission_codes=["order:update", "payment:read"],
        starts_at=starts,
        ends_at=ends,
    )
    await db_session.commit()
    codes = await RoleDelegationService.active_permission_codes(db_session, d.id)
    assert "order:update" in codes
    assert "payment:read" in codes


@pytest.mark.asyncio
async def test_order_archive_dry_run(db_session):
    from app.services.order_archive_service import OrderArchiveService

    result = await OrderArchiveService.archive_older_than(
        db_session, older_than_days=30, limit=10, dry_run=True
    )
    assert result["dry_run"] is True
    assert "candidates" in result


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_skip_locked_smoke(db_session):
    """仅在 MySQL CI job 中执行：验证 dialect 与 SKIP LOCKED 语法可用。"""
    from sqlalchemy import text

    bind = db_session.get_bind()
    if bind.dialect.name != "mysql":
        pytest.skip("not mysql")
    await db_session.execute(text("SELECT 1 FROM dual FOR UPDATE SKIP LOCKED"))
