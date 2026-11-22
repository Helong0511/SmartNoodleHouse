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



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_mysql_trigger_installation_can_resume_after_first_trigger_committed(db_session):
    """Exercise information_schema checks against MySQL after a partial trigger install."""
    import uuid

    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind.sync_engine
    suffix = uuid.uuid4().hex[:12]
    insert_name = f"trg_test_cwg_i_{suffix}"
    delete_name = f"trg_test_cwg_d_{suffix}"
    insert_action = (
        "INSERT IGNORE INTO customer_write_gates (customer_id) VALUES (NEW.id)"
    )
    delete_action = "DELETE FROM customer_write_gates WHERE customer_id = OLD.id"
    insert_sql = (
        f"CREATE TRIGGER {insert_name} AFTER INSERT ON customers FOR EACH ROW "
        f"{insert_action}"
    )
    delete_sql = (
        f"CREATE TRIGGER {delete_name} AFTER DELETE ON customers FOR EACH ROW "
        f"{delete_action}"
    )

    insert_args = {
        "name": insert_name,
        "table": "customers",
        "timing": "AFTER",
        "event": "INSERT",
        "action": insert_action,
        "create_sql": insert_sql,
    }
    delete_args = {
        "name": delete_name,
        "table": "customers",
        "timing": "AFTER",
        "event": "DELETE",
        "action": delete_action,
        "create_sql": delete_sql,
    }

    try:
        with engine.connect() as conn:
            # Simulate a failed migration after its first CREATE TRIGGER committed.
            migration._ensure_mysql_trigger(conn, **insert_args)
            migration._ensure_mysql_trigger(conn, **insert_args)
            migration._ensure_mysql_trigger(conn, **delete_args)
            # A complete retry must accept both matching definitions without DDL.
            migration._ensure_mysql_trigger(conn, **insert_args)
            migration._ensure_mysql_trigger(conn, **delete_args)

            found = conn.execute(
                migration.sa.text(
                    "SELECT COUNT(*) FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE() "
                    "AND TRIGGER_NAME IN (:insert_name, :delete_name)"
                ),
                {"insert_name": insert_name, "delete_name": delete_name},
            ).scalar_one()
            assert int(found) == 2
    finally:
        with engine.connect() as conn:
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {delete_name}")
            conn.exec_driver_sql(f"DROP TRIGGER IF EXISTS {insert_name}")
