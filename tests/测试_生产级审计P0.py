"""P0 回归：JWT 保留声明不可被 extra 覆盖；游客禁止储值财产操作。"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.customer_service import CustomerService


def test_jwt_extra_cannot_override_reserved_claims():
    from app.core.security import create_access_token, decode_token

    with patch("app.core.security.get_settings") as gs:
        settings = gs.return_value
        settings.jwt_secret = "z" * 48
        settings.jwt_algorithm = "HS256"
        settings.jwt_expire_minutes = 60
        settings.app_name = "SmartNoodleHouse"
        settings.jwt_audience = "smart-noodle-house"

        with patch(
            "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
            return_value=60,
        ):
            token = create_access_token(
                42,
                {
                    "sub": "999",
                    "typ": "customer",
                    "exp": 1,
                    "aud": "evil",
                    "iss": "evil",
                    "jti": "fixed",
                    "ver": 99,
                    "role": "SUPER_ADMIN",
                    "store_id": 7,
                },
                token_version=3,
            )
            payload = decode_token(token)

    assert payload["sub"] == "42"
    assert payload["typ"] == "staff"
    assert payload["aud"] == "smart-noodle-house"
    assert payload["iss"] == "SmartNoodleHouse"
    assert payload["ver"] == 3
    assert payload["jti"] != "fixed"
    assert payload["role"] == "SUPER_ADMIN"
    assert payload["store_id"] == 7
    assert payload["exp"] != 1


def test_guest_is_not_member():
    class G:
        member_status = "GUEST"

    class M:
        member_status = "MEMBER"

    assert CustomerService.is_member(G()) is False
    assert CustomerService.is_member(M()) is True


@pytest.mark.asyncio
async def test_balance_debit_rejects_guest():
    from app.services.balance_service import BalanceService

    guest = MagicMock()
    guest.member_status = "GUEST"
    guest.balance_gift = Decimal("100")
    guest.balance_principal = Decimal("100")

    empty = MagicMock()
    empty.scalar_one_or_none = MagicMock(return_value=None)
    locked = MagicMock()
    locked.scalar_one_or_none = MagicMock(return_value=guest)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[locked, empty])

    with pytest.raises(AppException) as ei:
        await BalanceService.debit_for_order(
            db,
            customer_id=1,
            order_id=10,
            amount=Decimal("10.00"),
            store_id=1,
        )
    assert ei.value.code == 40320


def test_baseline_reads_struct_sql_canonical_section():
    from pathlib import Path
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions" / "0001_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_0001", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    schema_path = mod._schema_sql_path()
    assert schema_path.name == "struct.sql"
    assert schema_path.is_file()
    raw = schema_path.read_text(encoding="utf-8")
    section = mod._extract_schema_section(raw)
    assert "CREATE TABLE" in section
    assert "DROP DATABASE" not in section
    assert "IDENTIFIED BY" not in section
    # Schema migration/DDL 不应包含数据库用户口令或要求部署者把口令写进 SQL。
    assert "IDENTIFIED BY" not in raw
    assert "REPLACE_WITH_STRONG_DB_PASSWORD" not in raw
