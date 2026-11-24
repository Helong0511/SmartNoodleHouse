"""Regression tests for the flash-sale claim cancellation-reason migration."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0054_flash_sale_claim_reason.py"
)
SPEC = importlib.util.spec_from_file_location(
    "migration_0054_flash_sale_claim_reason", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_flash_sale_claim_reason_migration_is_idempotent_and_reversible():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE marketing_flash_sale_claims (
                    id INTEGER PRIMARY KEY,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.upgrade()
                # A retry after a partially applied DDL step must be harmless.
                migration.upgrade()

            columns = {
                column["name"]: column
                for column in sa.inspect(conn).get_columns("marketing_flash_sale_claims")
            }
            assert "status_reason" in columns
            assert columns["status_reason"]["nullable"] is True
            assert isinstance(columns["status_reason"]["type"], sa.String)
            assert columns["status_reason"]["type"].length == 40

            with Operations.context(context):
                migration.downgrade()

            columns_after_downgrade = {
                column["name"]
                for column in sa.inspect(conn).get_columns("marketing_flash_sale_claims")
            }
            assert "status_reason" not in columns_after_downgrade
    finally:
        engine.dispose()
