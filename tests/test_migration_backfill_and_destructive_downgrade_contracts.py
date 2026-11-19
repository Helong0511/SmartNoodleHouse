"""Contract tests for lossy backfills and destructive migration downgrades."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


ROOT = Path(__file__).resolve().parents[1]


def _load(path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0019_null_backfill_defaults_are_explicit_and_observable(monkeypatch):
    migration = _load(
        "backend/alembic/versions/0019_three_way_alignment.py", "migration_0019_contract"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE delivery_orders ("
                "id INTEGER PRIMARY KEY, platform_fee NUMERIC NULL, "
                "packing_fee NUMERIC NULL, delivery_fee NUMERIC NULL, refund_status TEXT NULL)"
            )
            conn.exec_driver_sql(
                "INSERT INTO delivery_orders VALUES (1, NULL, NULL, NULL, NULL)"
            )
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(conn))
            )
            migration._set_not_null(conn, "delivery_orders", "platform_fee", "0.00")
            migration._set_not_null(conn, "delivery_orders", "packing_fee", "0.00")
            migration._set_not_null(conn, "delivery_orders", "delivery_fee", "0.00")
            migration._set_not_null(conn, "delivery_orders", "refund_status", "'NONE'")
            row = conn.exec_driver_sql(
                "SELECT platform_fee, packing_fee, delivery_fee, refund_status "
                "FROM delivery_orders WHERE id = 1"
            ).one()
            assert tuple(row) == (0, 0, 0, "NONE")
    finally:
        engine.dispose()


def test_0019_downgrade_recreates_single_column_financial_unique_indexes():
    source = (
        ROOT / "backend/alembic/versions/0019_three_way_alignment.py"
    ).read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    for index_name, column in (
        ("uk_fl_payment", "payment_id"),
        ("uk_fl_refund", "refund_id"),
        ("uk_fl_delivery_refund", "delivery_refund_id"),
    ):
        assert f'"{index_name}"' in downgrade
        assert f'"{column}"' in downgrade


def test_0036_downgrade_explicitly_drops_business_data_tables():
    source = (
        ROOT / "backend/alembic/versions/0036_audit_remaining_features.py"
    ).read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    for table in (
        "archived_order_snapshots",
        "payment_reconcile_diffs",
        "payment_reconcile_batches",
        "role_delegations",
    ):
        assert f'"{table}"' in downgrade
        assert f"DROP TABLE {table}" in downgrade


def test_0040_downgrade_drops_marketing_claim_and_order_attribution_data():
    source = (
        ROOT / "backend/alembic/versions/0040_flash_sale_marketing.py"
    ).read_text(encoding="utf-8")
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    for table in (
        "marketing_flash_sale_claims",
        "marketing_flash_sale_stocks",
        "marketing_flash_sales",
        "marketing_campaign_stores",
    ):
        assert f'"{table}"' in downgrade
        assert f"DROP TABLE IF EXISTS {table}" in downgrade
    for column in ("flash_claim_id", "marketing_campaign_id", "non_revenue"):
        assert f'"{column}"' in downgrade
        assert f"DROP COLUMN `{column}`" in downgrade
    assert "DROP COLUMN `code_key`" in downgrade
