"""餐品库存/价格 CHECK 约束

Revision ID: 0009_product_checks
Revises: 0008_coupon_template_store
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0009_product_checks"
down_revision = "0008_coupon_template_store"
branch_labels = None
depends_on = None


def _constraint_exists(conn, table: str, name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :t AND constraint_name = :n
            """
        ),
        {"t": table, "n": name},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    specs = [
        ("products", "chk_products_stock_nonneg", "CHECK (stock >= 0)"),
        ("products", "chk_products_price_pos", "CHECK (price > 0)"),
        (
            "products",
            "chk_products_orig_price",
            "CHECK (original_price IS NULL OR original_price >= 0)",
        ),
        ("store_product_stocks", "chk_sps_stock_nonneg", "CHECK (stock >= 0)"),
        ("options", "chk_options_extra_price", "CHECK (extra_price >= 0)"),
    ]
    for table, name, expr in specs:
        if _constraint_exists(conn, table, name):
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} {expr}"))


def downgrade() -> None:
    conn = op.get_bind()
    for table, name in [
        ("products", "chk_products_stock_nonneg"),
        ("products", "chk_products_price_pos"),
        ("products", "chk_products_orig_price"),
        ("store_product_stocks", "chk_sps_stock_nonneg"),
        ("options", "chk_options_extra_price"),
    ]:
        if _constraint_exists(conn, table, name):
            conn.execute(text(f"ALTER TABLE {table} DROP CHECK {name}"))
