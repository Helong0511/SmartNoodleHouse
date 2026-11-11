"""同桌就餐局 + 共享购物车表。

Revision ID: 0035_table_party_shared_cart
Revises: 0034_points_exchange_expire
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0035_table_party_shared_cart"
down_revision = "0034_points_exchange_expire"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).scalar()
        return bool(row)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    conn = op.get_bind()
    # These canonical parent tables are referenced by the three new tables.
    # Preflight all of them before MySQL's independently committed DDL starts.
    required_parent_tables = ("stores", "tables", "customers", "products")
    missing_parent_tables = [
        table for table in required_parent_tables if not _table_exists(conn, table)
    ]
    if missing_parent_tables:
        missing = ", ".join(missing_parent_tables)
        raise RuntimeError(
            "Cannot apply 0035_table_party_shared_cart: required canonical "
            f"parent table(s) missing: {missing}. Restore the canonical baseline before retrying."
        )
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS table_parties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id INTEGER NOT NULL,
                    table_id INTEGER NOT NULL,
                    host_customer_id INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
                    revision INTEGER NOT NULL DEFAULT 1,
                    expires_at DATETIME NOT NULL,
                    closed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (store_id) REFERENCES stores(id),
                    FOREIGN KEY (table_id) REFERENCES tables(id),
                    FOREIGN KEY (host_customer_id) REFERENCES customers(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS table_party_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    party_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'MEMBER',
                    nickname_snap VARCHAR(100),
                    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    left_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (party_id, customer_id),
                    FOREIGN KEY (party_id) REFERENCES table_parties(id) ON DELETE CASCADE,
                    FOREIGN KEY (customer_id) REFERENCES customers(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS shared_cart_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    party_id INTEGER NOT NULL,
                    added_by_customer_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name VARCHAR(100) NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    unit_price NUMERIC(10, 2) NOT NULL,
                    option_ids JSON,
                    option_text VARCHAR(255),
                    remark VARCHAR(200),
                    category_id INTEGER,
                    main_image VARCHAR(500),
                    no_member_discount INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (party_id) REFERENCES table_parties(id) ON DELETE CASCADE,
                    FOREIGN KEY (added_by_customer_id) REFERENCES customers(id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
                """
            )
        )
        return
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS table_parties (
              id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              store_id bigint UNSIGNED NOT NULL,
              table_id bigint UNSIGNED NOT NULL,
              host_customer_id bigint UNSIGNED NOT NULL,
              status varchar(20) NOT NULL DEFAULT 'OPEN',
              revision int NOT NULL DEFAULT 1,
              expires_at datetime(3) NOT NULL,
              closed_at datetime(3) NULL DEFAULT NULL,
              created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (id),
              INDEX idx_tp_store_table_status (store_id, table_id, status),
              CONSTRAINT fk_tp_store FOREIGN KEY (store_id) REFERENCES stores (id),
              CONSTRAINT fk_tp_table FOREIGN KEY (table_id) REFERENCES tables (id),
              CONSTRAINT fk_tp_host FOREIGN KEY (host_customer_id) REFERENCES customers (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS table_party_members (
              id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              party_id bigint UNSIGNED NOT NULL,
              customer_id bigint UNSIGNED NOT NULL,
              role varchar(20) NOT NULL DEFAULT 'MEMBER',
              nickname_snap varchar(100) NULL DEFAULT NULL,
              joined_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              left_at datetime(3) NULL DEFAULT NULL,
              created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (id),
              UNIQUE KEY uk_tpm_party_customer (party_id, customer_id),
              CONSTRAINT fk_tpm_party FOREIGN KEY (party_id) REFERENCES table_parties (id) ON DELETE CASCADE,
              CONSTRAINT fk_tpm_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS shared_cart_items (
              id bigint UNSIGNED NOT NULL AUTO_INCREMENT,
              party_id bigint UNSIGNED NOT NULL,
              added_by_customer_id bigint UNSIGNED NOT NULL,
              product_id bigint UNSIGNED NOT NULL,
              product_name varchar(100) NOT NULL,
              quantity int NOT NULL DEFAULT 1,
              unit_price decimal(10, 2) NOT NULL,
              option_ids json NULL,
              option_text varchar(255) NULL DEFAULT NULL,
              remark varchar(200) NULL DEFAULT NULL,
              category_id bigint UNSIGNED NULL DEFAULT NULL,
              main_image varchar(500) NULL DEFAULT NULL,
              no_member_discount tinyint NOT NULL DEFAULT 0,
              created_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
              updated_at datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
              PRIMARY KEY (id),
              INDEX idx_sci_party (party_id),
              CONSTRAINT fk_sci_party FOREIGN KEY (party_id) REFERENCES table_parties (id) ON DELETE CASCADE,
              CONSTRAINT fk_sci_adder FOREIGN KEY (added_by_customer_id) REFERENCES customers (id),
              CONSTRAINT fk_sci_product FOREIGN KEY (product_id) REFERENCES products (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    for t in ("shared_cart_items", "table_party_members", "table_parties"):
        if _table_exists(conn, t):
            conn.execute(text(f"DROP TABLE {t}"))
