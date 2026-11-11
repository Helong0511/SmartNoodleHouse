"""Regression coverage for migration 0042 during partial-schema rollback."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0042_privacy_opt_in_default.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0042_partial_rollback", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_is_safe_when_customers_table_is_absent(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            module = _load_migration()
            monkeypatch.setattr(
                module, "op", Operations(MigrationContext.configure(conn))
            )
            module.downgrade()
            assert not sa.inspect(conn).has_table("customers")
    finally:
        engine.dispose()


def test_downgrade_preserves_explicit_opt_in_default(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE customers (id INTEGER PRIMARY KEY, "
                "recommend_opt_in INTEGER NOT NULL DEFAULT 0)"
            )
            module = _load_migration()
            monkeypatch.setattr(
                module, "op", Operations(MigrationContext.configure(conn))
            )
            module.downgrade()
            conn.exec_driver_sql("INSERT INTO customers (id) VALUES (1)")
            assert conn.exec_driver_sql(
                "SELECT recommend_opt_in FROM customers WHERE id = 1"
            ).scalar_one() == 0
    finally:
        engine.dispose()
