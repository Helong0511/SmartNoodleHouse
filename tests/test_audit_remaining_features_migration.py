"""Regression coverage for partially applied migration 0036."""
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
    / "0036_audit_remaining_features.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0036_audit_remaining_features", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_recovers_missing_reconcile_diffs_table(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE products (id INTEGER PRIMARY KEY)")
            # Simulate interruption after the first table in the pair committed.
            conn.exec_driver_sql(
                """
                CREATE TABLE payment_reconcile_batches (
                    id INTEGER PRIMARY KEY,
                    channel VARCHAR(20) NOT NULL,
                    bill_date DATE NOT NULL
                )
                """
            )

            module = _load_migration()
            monkeypatch.setattr(
                module, "op", Operations(MigrationContext.configure(conn))
            )

            module.upgrade()
            module.upgrade()

            tables = set(sa.inspect(conn).get_table_names())
            assert "payment_reconcile_batches" in tables
            assert "payment_reconcile_diffs" in tables
            assert "role_delegations" in tables
            assert "archived_order_snapshots" in tables
            product_columns = {
                column["name"] for column in sa.inspect(conn).get_columns("products")
            }
            assert "cost_price" in product_columns
    finally:
        engine.dispose()
