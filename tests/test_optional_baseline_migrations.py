"""Regression tests for optional baseline tables during migrations 0047/0049/0050."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = {
    "0047": ROOT / "backend/alembic/versions/0047_payment_refund_idempotency_key.py",
    "0049": ROOT / "backend/alembic/versions/0049_menu_cache_outbox_retry.py",
    "0050": ROOT / "backend/alembic/versions/0050_menu_db_generation.py",
}


def _load(revision):
    spec = importlib.util.spec_from_file_location(f"migration_{revision}_absent_table", MIGRATIONS[revision])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_skips_missing_canonical_baseline_tables():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            for revision in ("0047", "0049", "0050"):
                module = _load(revision)
                module.op = Operations(MigrationContext.configure(conn))
                module.upgrade()

            assert not sa.inspect(conn).has_table("payment_refunds")
            assert not sa.inspect(conn).has_table("outbox_events")
            assert not sa.inspect(conn).has_table("stores")
    finally:
        engine.dispose()
