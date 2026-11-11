"""Regression tests for lossless downgrade of worker lease timestamp precision."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0044_worker_lease_timestamp_precision.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0044_worker_lease_timestamp_precision", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Connection:
    dialect = SimpleNamespace(name="mysql")

    def __init__(self, fractional_tables=()):
        self.fractional_tables = set(fractional_tables)

    def execute(self, statement):
        sql = str(statement)
        table = next(name for name in ("outbox_events", "webhook_inbox") if f"`{name}`" in sql)
        return _Result(table in self.fractional_tables)


class _Inspector:
    def has_table(self, table):
        return table in {"outbox_events", "webhook_inbox"}

    def get_columns(self, table):
        return [{"name": "id"}, {"name": "locked_at"}]


def test_downgrade_refuses_to_truncate_existing_microseconds(monkeypatch):
    migration = _load_migration()
    conn = _Connection({"webhook_inbox"})
    alterations = []
    monkeypatch.setattr(migration.sa, "inspect", lambda _conn: _Inspector())
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: conn,
            alter_column=lambda *args, **kwargs: alterations.append((args, kwargs)),
        ),
    )

    with pytest.raises(RuntimeError, match="microsecond values exist"):
        migration.downgrade()

    # Preflight must happen before either table is altered.
    assert alterations == []


def test_downgrade_converts_both_tables_when_no_microseconds_exist(monkeypatch):
    migration = _load_migration()
    conn = _Connection()
    alterations = []
    monkeypatch.setattr(migration.sa, "inspect", lambda _conn: _Inspector())
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: conn,
            alter_column=lambda *args, **kwargs: alterations.append((args, kwargs)),
        ),
    )

    migration.downgrade()

    assert [item[0][0] for item in alterations] == [
        "outbox_events",
        "webhook_inbox",
    ]


def test_upgrade_fails_when_a_required_worker_table_is_missing(monkeypatch):
    migration = _load_migration()
    conn = _Connection()
    alterations = []

    class _PartialInspector:
        def has_table(self, table):
            return table == "webhook_inbox"

        def get_columns(self, table):
            return [{"name": "id"}, {"name": "locked_at"}]

    monkeypatch.setattr(migration.sa, "inspect", lambda _conn: _PartialInspector())
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: conn,
            alter_column=lambda *args, **kwargs: alterations.append((args, kwargs)),
        ),
    )

    with pytest.raises(RuntimeError, match="required table 'outbox_events'"):
        migration.upgrade()
    assert alterations == []
