"""Exercise migration 0050: durable menu generation survives Redis-only loss."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0050_menu_db_generation.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0050_menu_db_generation", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_is_idempotent_and_backfills_generation_for_existing_stores(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE stores (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)"
            )
            conn.exec_driver_sql("INSERT INTO stores (id, name) VALUES (1, '旧门店')")
            module = _load_migration()
            monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))

            module.upgrade()
            module.upgrade()

            row = conn.exec_driver_sql(
                "SELECT menu_generation, menu_schedule_id FROM stores WHERE id=1"
            ).one()
            assert tuple(row) == (1_000_000_000, -1)

            # A second existing store is also initialized to a safe non-legacy version.
            conn.exec_driver_sql("INSERT INTO stores (id, name) VALUES (2, '新门店')")
            row2 = conn.exec_driver_sql(
                "SELECT menu_generation, menu_schedule_id FROM stores WHERE id=2"
            ).one()
            assert tuple(row2) == (1_000_000_000, -1)
    finally:
        engine.dispose()
