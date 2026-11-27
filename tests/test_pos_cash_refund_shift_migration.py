"""Regression coverage for migration 0053's POS cash refund shift link."""
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
    / "0053_pos_cash_refund_shift.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0053_pos_cash_refund_shift", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, operation: str) -> None:
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(conn))
    getattr(migration, operation)()


def _create_schema(conn, *, with_shift_table: bool = True) -> None:
    if with_shift_table:
        conn.exec_driver_sql("CREATE TABLE cashier_shifts (id INTEGER PRIMARY KEY)")
    conn.exec_driver_sql(
        "CREATE TABLE payment_refunds (id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL)"
    )


def test_upgrade_adds_nullable_shift_reference_index_and_foreign_key():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn)
            _run(conn, "upgrade")
            _run(conn, "upgrade")
            inspector = sa.inspect(conn)
            columns = {item["name"]: item for item in inspector.get_columns("payment_refunds")}
            assert columns["cashier_shift_id"]["nullable"] is True
            assert "ix_pr_cashier_shift_status" in {
                item["name"] for item in inspector.get_indexes("payment_refunds")
            }
            assert any(
                item.get("name") == "fk_payment_refunds_cashier_shift_id"
                and item["referred_table"] == "cashier_shifts"
                for item in inspector.get_foreign_keys("payment_refunds")
            )
    finally:
        engine.dispose()


def test_upgrade_supports_legacy_installation_without_shift_table():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn, with_shift_table=False)
            _run(conn, "upgrade")
            inspector = sa.inspect(conn)
            assert "cashier_shift_id" in {
                item["name"] for item in inspector.get_columns("payment_refunds")
            }
            assert not inspector.get_foreign_keys("payment_refunds")
    finally:
        engine.dispose()


def test_downgrade_drops_foreign_key_before_index_and_column():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn)
            _run(conn, "upgrade")
            _run(conn, "downgrade")
            inspector = sa.inspect(conn)
            assert {item["name"] for item in inspector.get_columns("payment_refunds")} == {
                "id",
                "status",
            }
            assert not inspector.get_indexes("payment_refunds")
            assert not inspector.get_foreign_keys("payment_refunds")
    finally:
        engine.dispose()


def test_upgrade_is_safe_when_refund_table_is_absent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _run(conn, "upgrade")
            assert not sa.inspect(conn).has_table("payment_refunds")
    finally:
        engine.dispose()


def test_upgrade_widens_pos_refund_request_status_for_reconciliation():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn)
            conn.exec_driver_sql(
                "CREATE TABLE pos_refund_requests (id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL DEFAULT 'PENDING')"
            )
            _run(conn, "upgrade")
            status = {
                item["name"]: item for item in sa.inspect(conn).get_columns("pos_refund_requests")
            }["status"]
            assert status["type"].length == 32
            # A second upgrade must remain idempotent.
            _run(conn, "upgrade")
            status = {
                item["name"]: item for item in sa.inspect(conn).get_columns("pos_refund_requests")
            }["status"]
            assert status["type"].length == 32
    finally:
        engine.dispose()
