"""Exercise migration 0049: only menu-cache invalidations get durable retries."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0049_menu_cache_outbox_retry.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0049_menu_cache_retry", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_extends_only_menu_cache_invalidation_retry_budget(monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE outbox_events (
                    id INTEGER PRIMARY KEY,
                    aggregate_type VARCHAR(32) NOT NULL,
                    event_kind VARCHAR(64) NOT NULL,
                    max_attempts INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR(16) NOT NULL,
                    next_retry_at DATETIME
                )
                """
            )
            conn.exec_driver_sql(
                """
                INSERT INTO outbox_events
                    (id, aggregate_type, event_kind, max_attempts, attempt_count, status)
                VALUES
                    (1, 'menu_cache', 'invalidate', 8, 8, 'FAILED'),
                    (2, 'reco_cache', 'invalidate_abc', 8, 8, 'FAILED'),
                    (3, 'webhook_task', 'execute', 8, 8, 'FAILED')
                """
            )

            module = _load_migration()
            monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(conn)))
            module.upgrade()
            module.upgrade()  # idempotent for repeated deployment/recovery

            rows = conn.exec_driver_sql(
                "SELECT id, max_attempts FROM outbox_events ORDER BY id"
            ).all()
            assert [tuple(row) for row in rows] == [
                (1, 2_147_483_647),
                (2, 8),
                (3, 8),
            ]

            # Downgrade must not re-dead-letter events that already exceeded 8 attempts.
            module.downgrade()
            retry_budget = conn.exec_driver_sql(
                "SELECT max_attempts FROM outbox_events WHERE id=1"
            ).scalar_one()
            assert retry_budget == 2_147_483_647
    finally:
        engine.dispose()
