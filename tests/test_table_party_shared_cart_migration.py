"""Regression coverage for recovering a partially applied migration 0035."""
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
    / "0035_table_party_shared_cart.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0035_table_party_shared_cart", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_recovers_when_only_table_parties_exists(monkeypatch):
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            for ddl in (
                "CREATE TABLE stores (id INTEGER PRIMARY KEY)",
                "CREATE TABLE tables (id INTEGER PRIMARY KEY)",
                "CREATE TABLE customers (id INTEGER PRIMARY KEY)",
                "CREATE TABLE products (id INTEGER PRIMARY KEY)",
                # Simulate MySQL DDL having committed the first table before
                # the process stopped and Alembic could record the revision.
                "CREATE TABLE table_parties (id INTEGER PRIMARY KEY)",
            ):
                conn.exec_driver_sql(ddl)

            module = _load_migration()
            monkeypatch.setattr(
                module, "op", Operations(MigrationContext.configure(conn))
            )

            module.upgrade()
            module.upgrade()  # a recovery retry must also be safe

            tables = set(sa.inspect(conn).get_table_names())
            assert {
                "table_parties",
                "table_party_members",
                "shared_cart_items",
            }.issubset(tables)

            member_columns = {
                column["name"]
                for column in sa.inspect(conn).get_columns("table_party_members")
            }
            cart_columns = {
                column["name"]
                for column in sa.inspect(conn).get_columns("shared_cart_items")
            }
            assert {"party_id", "customer_id"}.issubset(member_columns)
            assert {"party_id", "product_id", "quantity"}.issubset(cart_columns)
    finally:
        engine.dispose()
