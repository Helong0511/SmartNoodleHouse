"""Regression coverage for migration 0046 downgrade after partial application."""
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
    / "0046_outbox_stock_sync_version.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0046_outbox_stock_sync_version", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_downgrade(conn):
    migration = _load_migration()
    migration.op = Operations(MigrationContext.configure(conn))
    migration.downgrade()


def test_downgrade_is_safe_when_sync_version_column_is_absent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE outbox_events (id INTEGER PRIMARY KEY, event_kind TEXT)"
            )
            _run_downgrade(conn)
            columns = {item["name"] for item in sa.inspect(conn).get_columns("outbox_events")}
            assert columns == {"id", "event_kind"}
    finally:
        engine.dispose()


def test_downgrade_removes_sync_version_when_present():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE outbox_events (
                    id INTEGER PRIMARY KEY,
                    event_kind TEXT,
                    sync_version INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            _run_downgrade(conn)
            columns = {item["name"] for item in sa.inspect(conn).get_columns("outbox_events")}
            assert columns == {"id", "event_kind"}
    finally:
        engine.dispose()


def test_downgrade_is_safe_when_outbox_events_table_is_absent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _run_downgrade(conn)
            assert sa.inspect(conn).has_table("outbox_events") is False
    finally:
        engine.dispose()


def test_upgrade_fails_clearly_when_required_outbox_events_table_is_absent():
    import pytest

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        migration = _load_migration()
        with engine.begin() as conn:
            migration.op = Operations(MigrationContext.configure(conn))
            with pytest.raises(RuntimeError, match="required table.*outbox_events"):
                migration.upgrade()
            assert sa.inspect(conn).has_table("outbox_events") is False
    finally:
        engine.dispose()
