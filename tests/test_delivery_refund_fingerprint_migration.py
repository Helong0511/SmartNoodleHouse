"""Regression tests for migration 0043's refund fingerprint index validation."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
import pytest


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0043_delivery_refund_fingerprint.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0043_refund_fingerprint", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(conn, module):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module.op = Operations(MigrationContext.configure(conn))
    module.upgrade()


def _create_table(conn):
    conn.exec_driver_sql(
        """
        CREATE TABLE delivery_refunds (
            id INTEGER PRIMARY KEY,
            platform VARCHAR(32) NOT NULL,
            event_fingerprint VARCHAR(64)
        )
        """
    )


def test_upgrade_replaces_same_name_non_unique_index_with_required_unique_index():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_table(conn)
            conn.exec_driver_sql(
                "CREATE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (platform)"
            )

            module = _load_migration()
            _run_upgrade(conn, module)
            _run_upgrade(conn, module)

            exists, valid = module._index_state(conn)
            assert exists is True
            assert valid is True
    finally:
        engine.dispose()


def test_upgrade_replaces_same_name_unique_index_with_wrong_columns():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_table(conn)
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (event_fingerprint)"
            )

            module = _load_migration()
            _run_upgrade(conn, module)

            exists, valid = module._index_state(conn)
            assert exists is True
            assert valid is True
            columns = conn.exec_driver_sql(
                "PRAGMA index_info('uq_delivery_refund_event_fingerprint')"
            ).fetchall()
            assert [row[2] for row in columns] == ["platform", "event_fingerprint"]
    finally:
        engine.dispose()


def test_upgrade_keeps_exact_unique_index_unchanged():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_table(conn)
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (platform, event_fingerprint)"
            )

            module = _load_migration()
            _run_upgrade(conn, module)

            exists, valid = module._index_state(conn)
            assert exists is True
            assert valid is True
    finally:
        engine.dispose()


def test_upgrade_fails_clearly_when_required_delivery_refunds_table_is_absent():
    import pytest

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            module = _load_migration()
            with pytest.raises(RuntimeError, match="required table.*delivery_refunds"):
                _run_upgrade(conn, module)
            assert not sa.inspect(conn).has_table("delivery_refunds")
    finally:
        engine.dispose()


@pytest.mark.mysql
def test_mysql_upgrade_repairs_wrong_and_prefix_indexes_in_isolated_schema():
    """Exercise MySQL information_schema inspection against a real server."""
    import os
    import uuid

    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for the MySQL migration test")

    url = sa.engine.make_url(database_url).set(drivername="mysql+pymysql")
    schema_name = "refund_fp_migration_" + uuid.uuid4().hex
    admin_engine = sa.create_engine(url.set(database=None))
    schema_engine = None
    try:
        with admin_engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE DATABASE `" + schema_name + "` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        schema_engine = sa.create_engine(url.set(database=schema_name))
        with schema_engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE delivery_refunds ("
                "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, "
                "platform VARCHAR(32) NOT NULL, "
                "event_fingerprint VARCHAR(64) NULL) ENGINE=InnoDB"
            )
            conn.exec_driver_sql(
                "CREATE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (platform)"
            )
            module = _load_migration()
            _run_upgrade(conn, module)
            assert module._index_state(conn) == (True, True)

            conn.exec_driver_sql(
                "DROP INDEX uq_delivery_refund_event_fingerprint ON delivery_refunds"
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (event_fingerprint)"
            )
            _run_upgrade(conn, module)
            assert module._index_state(conn) == (True, True)

            conn.exec_driver_sql(
                "DROP INDEX uq_delivery_refund_event_fingerprint ON delivery_refunds"
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_delivery_refund_event_fingerprint "
                "ON delivery_refunds (platform(8), event_fingerprint(8))"
            )
            _run_upgrade(conn, module)
            assert module._index_state(conn) == (True, True)
            _run_upgrade(conn, module)
            assert module._index_state(conn) == (True, True)
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with admin_engine.begin() as conn:
            conn.exec_driver_sql("DROP DATABASE IF EXISTS `" + schema_name + "`")
        admin_engine.dispose()

