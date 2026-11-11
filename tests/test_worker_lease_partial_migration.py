"""Regression tests for migration 0044 with partially absent worker tables."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend" / "alembic" / "versions" / "0044_worker_lease_timestamp_precision.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0044_partial_worker_tables", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_and_downgrade_skip_absent_worker_tables(monkeypatch):
    migration = _load()
    conn = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    altered = []
    inspector = SimpleNamespace(
        has_table=lambda table: table == "webhook_inbox",
        get_columns=lambda table: [{"name": "id"}, {"name": "locked_at"}],
    )
    monkeypatch.setattr(migration.sa, "inspect", lambda _conn: inspector)
    monkeypatch.setattr(
        migration, "op",
        SimpleNamespace(get_bind=lambda: conn, alter_column=lambda *a, **kw: altered.append((a, kw))),
    )

    migration.upgrade()
    assert [item[0][0] for item in altered] == ["webhook_inbox"]

    altered.clear()
    migration.downgrade()
    assert [item[0][0] for item in altered] == ["webhook_inbox"]


def test_upgrade_and_downgrade_are_noops_when_both_worker_tables_are_absent(monkeypatch):
    migration = _load()
    conn = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    altered = []
    inspector = SimpleNamespace(has_table=lambda _table: False)
    monkeypatch.setattr(migration.sa, "inspect", lambda _conn: inspector)
    monkeypatch.setattr(
        migration, "op",
        SimpleNamespace(get_bind=lambda: conn, alter_column=lambda *a, **kw: altered.append((a, kw))),
    )

    migration.upgrade()
    migration.downgrade()
    assert altered == []
