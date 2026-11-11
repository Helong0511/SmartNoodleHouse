"""Regression coverage for migration 0040 campaign code-key collision handling."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "alembic"
    / "versions"
    / "0040_flash_sale_marketing.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0040_flash_sale_marketing", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duplicate_hq_code_suffix_cannot_collide_with_a_natural_code_key():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE marketing_campaigns (
                    id INTEGER PRIMARY KEY,
                    store_id INTEGER NULL,
                    code VARCHAR(64) NOT NULL,
                    code_key VARCHAR(96) NULL
                )
                """
            )
            # The first natural key is exactly the suffix the old algorithm
            # generated for row 3 when resolving the duplicate ALPHA code.
            conn.exec_driver_sql(
                "INSERT INTO marketing_campaigns(id, store_id, code) VALUES "
                "(1, NULL, 'ALPHA:#3'), "
                "(2, NULL, 'ALPHA'), "
                "(3, NULL, 'ALPHA')"
            )

            migration = _load_migration()
            migration._add_code_key(conn)

            rows = conn.exec_driver_sql(
                "SELECT id, code_key FROM marketing_campaigns ORDER BY id"
            ).all()
            keys = [row[1] for row in rows]
            assert len(keys) == len(set(keys)) == 3
            assert rows[0][1] == "HQ:ALPHA:#3"
            assert rows[2][1] != "HQ:ALPHA:#3"

            indexes = sa.inspect(conn).get_indexes("marketing_campaigns")
            unique_indexes = {
                index["name"]
                for index in indexes
                if index.get("unique") and index.get("column_names") == ["code_key"]
            }
            assert "uk_mc_code_key" in unique_indexes

            # A retry after an interrupted migration must produce the same keys.
            migration._add_code_key(conn)
            retried = conn.exec_driver_sql(
                "SELECT id, code_key FROM marketing_campaigns ORDER BY id"
            ).all()
            assert retried == rows
    finally:
        engine.dispose()
