"""Regression coverage for 0048 when its optional table is absent."""
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
    / "0048_reconcile_store_scope.py"
)


def test_downgrade_is_safe_when_reconcile_batches_table_is_absent(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "migration_0048_reconcile_store_scope", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(conn))
            )
            migration.downgrade()
            assert not sa.inspect(conn).has_table("payment_reconcile_batches")
    finally:
        engine.dispose()


def test_upgrade_fails_clearly_when_required_reconcile_table_is_absent(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "migration_0048_reconcile_store_scope_missing_upgrade", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(conn))
            )
            import pytest

            with pytest.raises(RuntimeError, match="required table.*payment_reconcile_batches"):
                migration.upgrade()
            assert not sa.inspect(conn).has_table("payment_reconcile_batches")
    finally:
        engine.dispose()
