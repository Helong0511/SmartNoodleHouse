"""Regression coverage for migration 0051 when optional POS refund table is absent."""
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
    / "0051_pos_refund_execution_fencing.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0051_pos_refund_execution_fencing", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_downgrade(conn):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(conn))
    migration.downgrade()


def test_downgrade_is_safe_when_optional_pos_refund_table_is_absent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _run_downgrade(conn)
            assert not sa.inspect(conn).has_table("pos_refund_requests")
    finally:
        engine.dispose()


def test_downgrade_removes_only_fencing_columns_when_table_exists():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE pos_refund_requests (
                    id INTEGER PRIMARY KEY,
                    status VARCHAR(20) NOT NULL,
                    execution_epoch BIGINT NOT NULL DEFAULT 0,
                    execution_token VARCHAR(64),
                    execution_lease_until DATETIME
                )
                """
            )
            _run_downgrade(conn)
            columns = {
                item["name"]
                for item in sa.inspect(conn).get_columns("pos_refund_requests")
            }
            assert columns == {"id", "status"}
    finally:
        engine.dispose()
