"""退款幂等键 Alembic 迁移的 SQLite 回归测试。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "alembic"
        / "versions"
        / "0047_payment_refund_idempotency_key.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0047_payment_refund_idempotency_key", migration_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(conn, migration):
    context = MigrationContext.configure(conn)
    with Operations.context(context):
        migration.upgrade()


def test_upgrade_adds_missing_refund_idempotency_column_and_unique_index():
    engine = create_engine("sqlite:///:memory:")
    migration = _load_migration()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE payment_refunds "
                    "(id INTEGER PRIMARY KEY, refund_no VARCHAR(40) NOT NULL)"
                )
            )
            _run_upgrade(conn, migration)

            columns = {
                row[1] for row in conn.exec_driver_sql(
                    "PRAGMA table_info(payment_refunds)"
                ).fetchall()
            }
            indexes = conn.exec_driver_sql(
                "PRAGMA index_list(payment_refunds)"
            ).fetchall()
            unique_columns = []
            for index in indexes:
                if bool(index[2]):
                    name = str(index[1]).replace("'", "''")
                    info = conn.exec_driver_sql(
                        f"PRAGMA index_info('{name}')"
                    ).fetchall()
                    unique_columns.append([row[2] for row in info])

            assert "idempotency_key" in columns
            assert ["idempotency_key"] in unique_columns
    finally:
        engine.dispose()


def test_upgrade_is_safe_when_canonical_schema_already_has_column_and_index():
    engine = create_engine("sqlite:///:memory:")
    migration = _load_migration()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE payment_refunds ("
                    "id INTEGER PRIMARY KEY, "
                    "refund_no VARCHAR(40) NOT NULL, "
                    "idempotency_key VARCHAR(64) NULL)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uk_refund_idempotency "
                    "ON payment_refunds (idempotency_key)"
                )
            )
            _run_upgrade(conn, migration)
            indexes = conn.exec_driver_sql(
                "PRAGMA index_list(payment_refunds)"
            ).fetchall()
            matching = [row for row in indexes if bool(row[2])]
            assert len(matching) == 1
    finally:
        engine.dispose()


def test_upgrade_refuses_duplicate_historical_refund_idempotency_keys():
    engine = create_engine("sqlite:///:memory:")
    migration = _load_migration()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE payment_refunds ("
                    "id INTEGER PRIMARY KEY, "
                    "refund_no VARCHAR(40) NOT NULL, "
                    "idempotency_key VARCHAR(64) NULL)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO payment_refunds (id, refund_no, idempotency_key) "
                    "VALUES (1, 'R1', 'duplicate-key'), "
                    "(2, 'R2', 'duplicate-key')"
                )
            )
            with pytest.raises(RuntimeError, match="拒绝自动删除退款数据"):
                _run_upgrade(conn, migration)
    finally:
        engine.dispose()
