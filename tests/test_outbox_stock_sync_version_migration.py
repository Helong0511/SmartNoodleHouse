"""Regression tests for migration 0046 with partially absent Outbox schema."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend" / "alembic" / "versions" / "0046_outbox_stock_sync_version.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0046_partial_outbox", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, direction):
    module = _load()
    module.op = Operations(MigrationContext.configure(conn))
    getattr(module, direction)()


def test_upgrade_fails_clearly_when_required_outbox_table_is_absent():
    import pytest

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            with pytest.raises(RuntimeError, match="required table.*outbox_events"):
                _run(conn, "upgrade")
            assert not sa.inspect(conn).has_table("outbox_events")
            # Downgrade remains safe after an interrupted/absent-table upgrade.
            _run(conn, "downgrade")
    finally:
        engine.dispose()


def test_downgrade_removes_sync_version_when_table_and_column_exist():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE outbox_events (id INTEGER PRIMARY KEY, sync_version INTEGER NOT NULL DEFAULT 0)"
            )
            _run(conn, "downgrade")
            assert [c["name"] for c in sa.inspect(conn).get_columns("outbox_events")] == ["id"]
    finally:
        engine.dispose()
