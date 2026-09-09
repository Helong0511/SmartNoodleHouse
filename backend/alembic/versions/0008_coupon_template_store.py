"""优惠券模板 store_id 门店隔离

Revision ID: 0008_coupon_template_store
Revises: 0007_prod_indexes
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0008_coupon_template_store"
down_revision = "0007_prod_indexes"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return int(row or 0) > 0


def _index_exists(conn, table: str, index: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :t AND index_name = :i
            """
        ),
        {"t": table, "i": index},
    ).scalar()
    return int(row or 0) > 0


def _fk_exists(conn, table: str, name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_schema = DATABASE()
              AND table_name = :t AND constraint_name = :n
              AND constraint_type = 'FOREIGN KEY'
            """
        ),
        {"t": table, "n": name},
    ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()
    if not _col_exists(conn, "coupon_templates", "store_id"):
        conn.execute(
            text(
                "ALTER TABLE coupon_templates "
                "ADD COLUMN store_id BIGINT UNSIGNED DEFAULT NULL "
                "COMMENT 'NULL=总部全店模板；非空=仅该门店' AFTER id"
            )
        )
    if not _index_exists(conn, "coupon_templates", "idx_coupon_tmpl_store"):
        conn.execute(text("ALTER TABLE coupon_templates ADD INDEX idx_coupon_tmpl_store (store_id)"))
    if not _fk_exists(conn, "coupon_templates", "fk_coupon_tmpl_store"):
        conn.execute(
            text(
                "ALTER TABLE coupon_templates "
                "ADD CONSTRAINT fk_coupon_tmpl_store FOREIGN KEY (store_id) REFERENCES stores(id)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _fk_exists(conn, "coupon_templates", "fk_coupon_tmpl_store"):
        conn.execute(text("ALTER TABLE coupon_templates DROP FOREIGN KEY fk_coupon_tmpl_store"))
    if _index_exists(conn, "coupon_templates", "idx_coupon_tmpl_store"):
        conn.execute(text("ALTER TABLE coupon_templates DROP INDEX idx_coupon_tmpl_store"))
    if _col_exists(conn, "coupon_templates", "store_id"):
        conn.execute(text("ALTER TABLE coupon_templates DROP COLUMN store_id"))
