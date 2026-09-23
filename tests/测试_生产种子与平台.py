"""sql/data.sql 与当前平台权限、系统参数、表清单对齐。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DATA_SQL = (ROOT / "sql" / "data.sql").read_text(encoding="utf-8")
STRUCT_SQL = (ROOT / "sql" / "struct.sql").read_text(encoding="utf-8")


def test_seed_covers_api_permissions_and_settings():
    from app.services.system_settings_service import BUSINESS_SETTING_META

    api_dir = ROOT / "backend" / "app" / "api"
    codes = set(re.findall(r'require_permission\("([^"]+)"\)', "\n".join(
        p.read_text(encoding="utf-8") for p in api_dir.rglob("*.py")
    )))
    codes.add("customer:write")
    missing_perm = sorted(c for c in codes if f"'{c}'" not in DATA_SQL)
    assert not missing_perm, missing_perm
    missing_keys = sorted(k for k in BUSINESS_SETTING_META if f"'{k}'" not in DATA_SQL)
    assert not missing_keys, missing_keys


def test_seed_truncates_every_struct_table():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", STRUCT_SQL)
    missing = [name for name in tables if f"TRUNCATE TABLE `{name}`" not in DATA_SQL]
    assert not missing, missing
