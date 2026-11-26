"""Regression tests for original-customer ledger migration 0054."""
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
    / "0054_customer_ledger_origin.py"
)


def _run(conn, operation: str) -> None:
    spec = importlib.util.spec_from_file_location("migration_0054", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(conn))
    getattr(module, operation)()


def _schema(conn) -> None:
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    conn.exec_driver_sql("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE point_ledger (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), delta INTEGER NOT NULL)")
    conn.exec_driver_sql("CREATE TABLE balance_ledger (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), delta_principal NUMERIC NOT NULL)")


def test_upgrade_backfills_original_customer_and_is_idempotent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _schema(conn)
            conn.exec_driver_sql("INSERT INTO customers(id) VALUES (1), (2)")
            conn.exec_driver_sql("INSERT INTO point_ledger(id, customer_id, delta) VALUES (11, 1, 80)")
            conn.exec_driver_sql("INSERT INTO balance_ledger(id, customer_id, delta_principal) VALUES (21, 2, 10)")
            _run(conn, "upgrade")
            _run(conn, "upgrade")
            assert conn.exec_driver_sql(
                "SELECT origin_customer_id FROM point_ledger WHERE id=11"
            ).scalar_one() == 1
            assert conn.exec_driver_sql(
                "SELECT origin_customer_id FROM balance_ledger WHERE id=21"
            ).scalar_one() == 2
            for table in ("point_ledger", "balance_ledger"):
                assert "origin_customer_id" in {
                    col["name"] for col in sa.inspect(conn).get_columns(table)
                }
                assert sa.inspect(conn).get_foreign_keys(table)
    finally:
        engine.dispose()


def test_downgrade_removes_origin_columns_and_indexes():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _schema(conn)
            conn.exec_driver_sql("INSERT INTO customers(id) VALUES (1)")
            _run(conn, "upgrade")
            _run(conn, "downgrade")
            for table in ("point_ledger", "balance_ledger"):
                assert "origin_customer_id" not in {
                    col["name"] for col in sa.inspect(conn).get_columns(table)
                }
    finally:
        engine.dispose()
