"""Safety checks for the canonical Alembic baseline migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "backend" / "alembic" / "versions" / "0001_baseline.py"


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0001_baseline_safety", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE IF EXISTS order_status_history",
        "DROP DATABASE smart_noodle",
        "DROP VIEW IF EXISTS v_orders",
        "TRUNCATE TABLE orders",
        "DELETE FROM orders",
        "GRANT ALL PRIVILEGES ON smart_noodle.* TO 'app'@'%'",
        "REVOKE ALL PRIVILEGES ON smart_noodle.* FROM 'app'@'%'",
    ],
)
def test_baseline_never_executes_destructive_or_privilege_statements(statement):
    assert _migration()._should_skip_statement(statement)


def test_baseline_allows_canonical_create_table_statements():
    assert not _migration()._should_skip_statement(
        "CREATE TABLE IF NOT EXISTS customers (id BIGINT PRIMARY KEY)"
    )


def test_current_canonical_schema_order_reset_is_never_executed():
    raw = (ROOT / "sql" / "struct.sql").read_text(encoding="utf-8")
    sql = _migration()._extract_schema_section(raw)
    statements = _migration()._split_sql_statements(sql)
    reset = next(stmt for stmt in statements if stmt.upper().startswith("DROP TABLE"))
    assert "order_status_history" in reset
    assert _migration()._should_skip_statement(reset)
