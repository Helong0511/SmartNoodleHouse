"""sql/data_sys.sql + sql/data_custom.sql 与当前平台权限、系统参数、表清单对齐。"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DATA_SYS_SQL = (ROOT / "sql" / "data_sys.sql").read_text(encoding="utf-8")
DATA_CUSTOM_SQL = (ROOT / "sql" / "data_custom.sql").read_text(encoding="utf-8")
DATA_SQL = DATA_SYS_SQL + "\n" + DATA_CUSTOM_SQL
STRUCT_SQL = (ROOT / "sql" / "struct.sql").read_text(encoding="utf-8")


def test_seed_covers_api_permissions_and_settings():
    from app.services.system_settings_service import BUSINESS_SETTING_META

    api_dir = ROOT / "backend" / "app" / "api"
    codes = set(re.findall(r'require_permission\("([^"]+)"\)', "\n".join(
        p.read_text(encoding="utf-8") for p in api_dir.rglob("*.py")
    )))
    codes.add("customer:write")
    missing_perm = sorted(c for c in codes if f"'{c}'" not in DATA_SYS_SQL)
    assert not missing_perm, missing_perm
    missing_keys = sorted(k for k in BUSINESS_SETTING_META if f"'{k}'" not in DATA_SYS_SQL)
    assert not missing_keys, missing_keys


def test_seed_truncates_every_struct_table():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", STRUCT_SQL)
    missing = [name for name in tables if f"TRUNCATE TABLE `{name}`" not in DATA_SYS_SQL]
    assert not missing, missing


def test_custom_seed_has_stores_not_truncate():
    assert "TRUNCATE TABLE" not in DATA_CUSTOM_SQL
    assert "SH01" in DATA_CUSTOM_SQL
    assert "product_categories" in DATA_CUSTOM_SQL
    # 人脸样例属本地联调，不得进生产门店种子
    assert "INSERT INTO `face_profiles`" not in DATA_CUSTOM_SQL
    assert "INSERT INTO `smart_noodle_house`.`face_profiles`" not in DATA_CUSTOM_SQL
    # 里程碑按券名关联，不硬编码 template_id 数字
    assert "满10单免2元" in DATA_CUSTOM_SQL
    assert "JOIN `coupon_templates`" in DATA_CUSTOM_SQL
    assert "env_version=develop" not in DATA_CUSTOM_SQL
    assert "YOUR_ALIPAY_APPID" not in DATA_CUSTOM_SQL
    # 员工角色按 code
    assert "`r`.`code` = 'STORE_ADMIN'" in DATA_CUSTOM_SQL
    assert "`r`.`code` = 'KITCHEN'" in DATA_CUSTOM_SQL


def test_struct_financial_and_delivery_indexes():
    assert "uk_dr_platform_refund" in STRUCT_SQL
    assert "uk_fl_payment" not in STRUCT_SQL
    assert "uk_fl_refund" not in STRUCT_SQL
    assert "uk_fl_delivery_refund" not in STRUCT_SQL
    assert "uq_fin_ledger_entry_payment" in STRUCT_SQL


def test_custom_seed_keeps_sh01_catalog_and_idempotent_style():
    product_inserts = len(re.findall(r"INSERT INTO `products`", DATA_CUSTOM_SQL))
    assert product_inserts >= 14, product_inserts
    assert "SH01-ZhaoPaiMian-ChangWangMian" in DATA_CUSTOM_SQL
    assert "INSERT INTO `option_groups`" in DATA_CUSTOM_SQL
    assert "INSERT INTO `options`" in DATA_CUSTOM_SQL
    # 门店种子禁止 VALUES(...) 批量写法，统一 SELECT + NOT EXISTS
    assert not re.search(r"INSERT INTO `[a-z_]+`[^;]*VALUES\s*\(", DATA_CUSTOM_SQL, re.I | re.S)
    assert DATA_SYS_SQL.count("TRUNCATE TABLE") == len(
        re.findall(r"CREATE TABLE IF NOT EXISTS `([^`]+)`", STRUCT_SQL)
    )
    assert len(re.findall(r"INSERT INTO `tables`", DATA_CUSTOM_SQL)) >= 3
    assert "env_version=release" in DATA_CUSTOM_SQL
    assert DATA_CUSTOM_SQL.count("alipay_qr_url") >= 3
    # 禁止硬编码自增主键（按业务键幂等，避免非空库撞 id）
    assert "INSERT INTO `stores` (`id`" not in DATA_CUSTOM_SQL
    assert "INSERT INTO `option_groups` (`id`" not in DATA_CUSTOM_SQL
    assert not re.search(r"SELECT\s+\d+\s*,\s*`s`\.`id`", DATA_CUSTOM_SQL)
    assert "INSERT INTO `users` (`id`" not in DATA_SYS_SQL
    assert "OR `u`.`id` = 1" not in DATA_SYS_SQL
    data_test = (ROOT / "sql" / "data_test.sql").read_text(encoding="utf-8")
    assert "INSERT INTO `customers` (\n  `id`" not in data_test
    assert "INSERT INTO `face_profiles` (`id`" not in data_test
    assert "fp.`id` = 1" not in data_test
