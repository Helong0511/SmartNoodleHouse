"""门店经纬度 + 规格组 store_id 隔离

Revision ID: 0011_store_geo_option_scope
Revises: 0010_coupon_ledger_idempotency
Create Date: 2026-09-10
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0011_store_geo_option_scope"
down_revision = "0010_coupon_ledger_idempotency"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, col: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": col},
    ).scalar()
    return int(row or 0) > 0


def _index_exists(conn, table: str, name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :t AND index_name = :n
            """
        ),
        {"t": table, "n": name},
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
    if not _col_exists(conn, "stores", "latitude"):
        conn.execute(
            text(
                "ALTER TABLE stores ADD COLUMN latitude DECIMAL(10,7) NULL "
                "COMMENT '纬度（天气口味）' AFTER phone"
            )
        )
    if not _col_exists(conn, "stores", "longitude"):
        conn.execute(
            text(
                "ALTER TABLE stores ADD COLUMN longitude DECIMAL(10,7) NULL "
                "COMMENT '经度（天气口味）' AFTER latitude"
            )
        )

    if not _col_exists(conn, "option_groups", "store_id"):
        conn.execute(
            text(
                "ALTER TABLE option_groups ADD COLUMN store_id BIGINT UNSIGNED NULL "
                "COMMENT 'NULL=总部共享；非空=门店私有' AFTER id"
            )
        )
    if not _fk_exists(conn, "option_groups", "fk_og_store"):
        conn.execute(
            text(
                "ALTER TABLE option_groups ADD CONSTRAINT fk_og_store "
                "FOREIGN KEY (store_id) REFERENCES stores(id)"
            )
        )

    # 去掉全局 code UNIQUE，改为按门店作用域唯一
    if _index_exists(conn, "option_groups", "code"):
        conn.execute(text("ALTER TABLE option_groups DROP INDEX code"))
    if _index_exists(conn, "option_groups", "uk_option_groups_code"):
        conn.execute(text("ALTER TABLE option_groups DROP INDEX uk_option_groups_code"))

    if not _col_exists(conn, "option_groups", "store_scope"):
        conn.execute(
            text(
                "ALTER TABLE option_groups ADD COLUMN store_scope BIGINT UNSIGNED "
                "AS (IFNULL(store_id, 0)) STORED COMMENT '唯一约束用：NULL store→0'"
            )
        )
    if not _index_exists(conn, "option_groups", "uk_og_store_code"):
        conn.execute(
            text("ALTER TABLE option_groups ADD UNIQUE KEY uk_og_store_code (store_scope, code)")
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "option_groups", "uk_og_store_code"):
        conn.execute(text("ALTER TABLE option_groups DROP INDEX uk_og_store_code"))
    if _col_exists(conn, "option_groups", "store_scope"):
        conn.execute(text("ALTER TABLE option_groups DROP COLUMN store_scope"))
    if _fk_exists(conn, "option_groups", "fk_og_store"):
        conn.execute(text("ALTER TABLE option_groups DROP FOREIGN KEY fk_og_store"))
    if _col_exists(conn, "option_groups", "store_id"):
        conn.execute(text("ALTER TABLE option_groups DROP COLUMN store_id"))
    if not _index_exists(conn, "option_groups", "code"):
        conn.execute(text("ALTER TABLE option_groups ADD UNIQUE KEY code (code)"))
    if _col_exists(conn, "stores", "longitude"):
        conn.execute(text("ALTER TABLE stores DROP COLUMN longitude"))
    if _col_exists(conn, "stores", "latitude"):
        conn.execute(text("ALTER TABLE stores DROP COLUMN latitude"))
