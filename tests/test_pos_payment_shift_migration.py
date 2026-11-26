"""Regression coverage for migration 0052's POS payment-to-shift link."""
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
    / "0052_pos_payment_shift.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0052_pos_payment_shift", MIGRATION_PATH
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
        conn.exec_driver_sql(
            "CREATE TABLE cashier_shifts (id INTEGER PRIMARY KEY)"
        )
    conn.exec_driver_sql(
        "CREATE TABLE payments ("
        "id INTEGER PRIMARY KEY, "
        "status VARCHAR(20) NOT NULL DEFAULT 'PENDING')"
    )


def test_upgrade_adds_nullable_shift_reference_index_and_foreign_key():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn)
            _run(conn, "upgrade")
            _run(conn, "upgrade")

            inspector = sa.inspect(conn)
            columns = {item["name"]: item for item in inspector.get_columns("payments")}
            assert columns["cashier_shift_id"]["nullable"] is True
            assert "ix_payments_cashier_shift_status" in {
                item["name"] for item in inspector.get_indexes("payments")
            }
            foreign_keys = inspector.get_foreign_keys("payments")
            assert any(
                item.get("name") == "fk_payments_cashier_shift_id"
                and item["referred_table"] == "cashier_shifts"
                and item["constrained_columns"] == ["cashier_shift_id"]
                for item in foreign_keys
            )
    finally:
        engine.dispose()


def test_upgrade_keeps_legacy_installation_without_shift_table_usable():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn, with_shift_table=False)
            _run(conn, "upgrade")
            inspector = sa.inspect(conn)
            assert "cashier_shift_id" in {
                item["name"] for item in inspector.get_columns("payments")
            }
            assert "ix_payments_cashier_shift_status" in {
                item["name"] for item in inspector.get_indexes("payments")
            }
            assert not inspector.get_foreign_keys("payments")
    finally:
        engine.dispose()


def test_downgrade_removes_only_shift_reference_schema():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_schema(conn)
            _run(conn, "upgrade")
            _run(conn, "downgrade")
            inspector = sa.inspect(conn)
            assert {item["name"] for item in inspector.get_columns("payments")} == {
                "id",
                "status",
            }
            assert not inspector.get_indexes("payments")
            assert not inspector.get_foreign_keys("payments")
    finally:
        engine.dispose()


def test_upgrade_is_safe_when_payments_table_is_absent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _run(conn, "upgrade")
            assert not sa.inspect(conn).has_table("payments")
    finally:
        engine.dispose()
