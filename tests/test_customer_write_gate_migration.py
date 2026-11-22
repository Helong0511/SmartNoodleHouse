"""Unit tests for retry-safe MySQL trigger installation in migration 0052."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0052_customer_write_gates.py"
)
SPEC = importlib.util.spec_from_file_location(
    "migration_0052_customer_write_gates", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class _MappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Connection:
    def __init__(self, existing=None):
        self.existing = existing
        self.executed = []
        self.created = []

    def execute(self, statement, params):
        self.executed.append((str(statement), params))
        return _MappingResult(self.existing)

    def exec_driver_sql(self, statement):
        self.created.append(statement)


TRIGGER = {
    "name": "trg_customers_create_write_gate",
    "table": "customers",
    "timing": "AFTER",
    "event": "INSERT",
    "action": (
        "INSERT IGNORE INTO customer_write_gates (customer_id) "
        "VALUES (NEW.id)"
    ),
    "create_sql": (
        "CREATE TRIGGER trg_customers_create_write_gate "
        "AFTER INSERT ON customers FOR EACH ROW "
        "INSERT IGNORE INTO customer_write_gates (customer_id) VALUES (NEW.id)"
    ),
}


def _ensure(conn):
    migration._ensure_mysql_trigger(conn, **TRIGGER)


def test_trigger_is_created_when_not_installed():
    conn = _Connection()

    _ensure(conn)

    assert len(conn.executed) == 1
    assert conn.executed[0][1] == {"name": TRIGGER["name"]}
    assert conn.created == [TRIGGER["create_sql"]]


def test_matching_existing_trigger_is_accepted_without_ddl():
    conn = _Connection(
        {
            "EVENT_OBJECT_TABLE": "customers",
            "ACTION_TIMING": "AFTER",
            "EVENT_MANIPULATION": "INSERT",
            # MySQL may return harmless quoting/whitespace differences.
            "ACTION_STATEMENT": (
                "INSERT IGNORE INTO `customer_write_gates` "
                "(`customer_id`) VALUES (NEW.id);"
            ),
        }
    )

    _ensure(conn)

    assert len(conn.executed) == 1
    assert conn.created == []


def test_same_name_trigger_with_different_definition_fails_closed():
    conn = _Connection(
        {
            "EVENT_OBJECT_TABLE": "customers",
            "ACTION_TIMING": "AFTER",
            "EVENT_MANIPULATION": "INSERT",
            "ACTION_STATEMENT": (
                "DELETE FROM customer_write_gates WHERE customer_id = NEW.id"
            ),
        }
    )

    with pytest.raises(RuntimeError, match="does not match migration 0052"):
        _ensure(conn)

    assert conn.created == []
