"""Exercise Alembic 0048 against both pre-scope and canonical fresh schemas."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0048_reconcile_store_scope.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0048_reconcile_scope", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(conn, monkeypatch):
    module = _load_migration()
    monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
    module.upgrade()


def test_upgrade_adds_store_scope_to_existing_legacy_schema(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE payment_reconcile_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel VARCHAR(20) NOT NULL,
                    bill_date DATE NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                )
                """
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uk_prb_channel_bill "
                "ON payment_reconcile_batches(channel, bill_date)"
            )
            conn.exec_driver_sql(
                "INSERT INTO payment_reconcile_batches(channel, bill_date, status) "
                "VALUES ('WECHAT', '2026-11-05', 'DONE')"
            )

            _run_upgrade(conn, monkeypatch)

            row = conn.exec_driver_sql(
                "SELECT scope_store_id FROM payment_reconcile_batches WHERE channel='WECHAT'"
            ).one()
            assert row[0] == 0

            conn.exec_driver_sql(
                "INSERT INTO payment_reconcile_batches(channel, bill_date, scope_store_id) "
                "VALUES ('WECHAT', '2026-11-05', 101)"
            )
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql(
                    "INSERT INTO payment_reconcile_batches(channel, bill_date, scope_store_id) "
                    "VALUES ('WECHAT', '2026-11-05', 101)"
                )
    finally:
        engine.dispose()


def test_upgrade_is_noop_when_canonical_baseline_already_has_store_scope(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE payment_reconcile_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel VARCHAR(20) NOT NULL,
                    bill_date DATE NOT NULL,
                    scope_store_id BIGINT NOT NULL DEFAULT 0,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                )
                """
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uk_prb_channel_bill_store "
                "ON payment_reconcile_batches(channel, bill_date, scope_store_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX ix_payment_reconcile_batches_scope_store_id "
                "ON payment_reconcile_batches(scope_store_id)"
            )
            _run_upgrade(conn, monkeypatch)

            names = {item["name"] for item in sa.inspect(conn).get_indexes("payment_reconcile_batches")}
            assert "uk_prb_channel_bill_store" in names
            assert "ix_payment_reconcile_batches_scope_store_id" in names
            assert "uk_prb_channel_bill" not in names
    finally:
        engine.dispose()
