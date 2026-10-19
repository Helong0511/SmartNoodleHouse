"""日常体验增强：催单/预约取餐/生日/收藏/发票/排队。

Revision ID: 0025_daily_ux_features
Revises: 0024_order_milestone_rewards
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0025_daily_ux_features"
down_revision = "0024_order_milestone_rewards"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).scalar()
    return row is not None


def _has_table(conn, table: str) -> bool:
    if conn.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).scalar()
    else:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ),
            {"t": table},
        ).scalar()
    return row is not None


def _add_columns(conn) -> None:
    sqlite = conn.dialect.name == "sqlite"
    column_specs = [
        # (table, column, sqlite_ddl, mysql_ddl)
        (
            "orders",
            "urge_count",
            "INTEGER NOT NULL DEFAULT 0",
            "`urge_count` int NOT NULL DEFAULT 0 COMMENT '顾客催单次数'",
        ),
        (
            "orders",
            "urged_at",
            "DATETIME NULL",
            "`urged_at` datetime NULL DEFAULT NULL COMMENT '最近催单时间'",
        ),
        (
            "orders",
            "scheduled_pickup_at",
            "DATETIME NULL",
            "`scheduled_pickup_at` datetime NULL DEFAULT NULL COMMENT '预约取餐时间（打包单）'",
        ),
        (
            "customers",
            "birthday",
            "DATE NULL",
            "`birthday` date NULL DEFAULT NULL COMMENT '生日（会员日权益）'",
        ),
    ]
    for table, column, sqlite_ddl, mysql_ddl in column_specs:
        if _has_column(conn, table, column):
            continue
        ddl = sqlite_ddl if sqlite else mysql_ddl
        if sqlite:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_ddl}"))
        else:
            conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN {ddl}"))


def _create_tables(conn) -> None:
    sqlite = conn.dialect.name == "sqlite"

    if not _has_table(conn, "customer_favorite_products"):
        if sqlite:
            conn.execute(
                text(
                    "CREATE TABLE customer_favorite_products ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "customer_id BIGINT NOT NULL REFERENCES customers(id) ON DELETE CASCADE, "
                    "product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE, "
                    "store_id BIGINT NOT NULL REFERENCES stores(id), "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT uq_cfp_customer_product UNIQUE (customer_id, product_id))"
                )
            )
            conn.execute(text("CREATE INDEX ix_cfp_customer_store ON customer_favorite_products (customer_id, store_id)"))
            conn.execute(text("CREATE INDEX ix_cfp_customer_id ON customer_favorite_products (customer_id)"))
            conn.execute(text("CREATE INDEX ix_cfp_product_id ON customer_favorite_products (product_id)"))
            conn.execute(text("CREATE INDEX ix_cfp_store_id ON customer_favorite_products (store_id)"))
        else:
            conn.execute(
                text(
                    "CREATE TABLE `customer_favorite_products` ("
                    "`id` bigint NOT NULL AUTO_INCREMENT, "
                    "`customer_id` bigint NOT NULL, "
                    "`product_id` bigint NOT NULL, "
                    "`store_id` bigint NOT NULL, "
                    "`created_at` datetime DEFAULT CURRENT_TIMESTAMP, "
                    "`updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "PRIMARY KEY (`id`), "
                    "UNIQUE KEY `uq_cfp_customer_product` (`customer_id`,`product_id`), "
                    "KEY `ix_cfp_customer_store` (`customer_id`,`store_id`), "
                    "KEY `ix_cfp_customer_id` (`customer_id`), "
                    "KEY `ix_cfp_product_id` (`product_id`), "
                    "KEY `ix_cfp_store_id` (`store_id`), "
                    "CONSTRAINT `fk_cfp_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`) ON DELETE CASCADE, "
                    "CONSTRAINT `fk_cfp_product` FOREIGN KEY (`product_id`) REFERENCES `products` (`id`) ON DELETE CASCADE, "
                    "CONSTRAINT `fk_cfp_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='菜品收藏'"
                )
            )

    if not _has_table(conn, "invoice_requests"):
        if sqlite:
            conn.execute(
                text(
                    "CREATE TABLE invoice_requests ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "order_id BIGINT NOT NULL REFERENCES orders(id), "
                    "order_no VARCHAR(40) NOT NULL, "
                    "store_id BIGINT NOT NULL REFERENCES stores(id), "
                    "customer_id BIGINT REFERENCES customers(id), "
                    "title_type VARCHAR(10) NOT NULL DEFAULT 'PERSON', "
                    "title VARCHAR(200) NOT NULL, "
                    "tax_no VARCHAR(50) NULL, "
                    "email VARCHAR(100) NOT NULL, "
                    "amount NUMERIC(10,2) NOT NULL DEFAULT 0, "
                    "status VARCHAR(20) NOT NULL DEFAULT 'PENDING', "
                    "reject_reason VARCHAR(255) NULL, "
                    "issued_at DATETIME NULL, "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT uq_invoice_order UNIQUE (order_id))"
                )
            )
            conn.execute(text("CREATE INDEX ix_invoice_requests_order_id ON invoice_requests (order_id)"))
            conn.execute(text("CREATE INDEX ix_invoice_requests_store_status ON invoice_requests (store_id, status)"))
            conn.execute(text("CREATE INDEX ix_invoice_requests_status ON invoice_requests (status)"))
        else:
            conn.execute(
                text(
                    "CREATE TABLE `invoice_requests` ("
                    "`id` bigint NOT NULL AUTO_INCREMENT, "
                    "`order_id` bigint NOT NULL, "
                    "`order_no` varchar(40) NOT NULL, "
                    "`store_id` bigint NOT NULL, "
                    "`customer_id` bigint NULL, "
                    "`title_type` varchar(10) NOT NULL DEFAULT 'PERSON', "
                    "`title` varchar(200) NOT NULL, "
                    "`tax_no` varchar(50) NULL DEFAULT NULL, "
                    "`email` varchar(100) NOT NULL, "
                    "`amount` decimal(10,2) NOT NULL DEFAULT '0.00', "
                    "`status` varchar(20) NOT NULL DEFAULT 'PENDING', "
                    "`reject_reason` varchar(255) NULL DEFAULT NULL, "
                    "`issued_at` datetime NULL DEFAULT NULL, "
                    "`created_at` datetime DEFAULT CURRENT_TIMESTAMP, "
                    "`updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "PRIMARY KEY (`id`), "
                    "UNIQUE KEY `uq_invoice_order` (`order_id`), "
                    "KEY `ix_invoice_requests_order_id` (`order_id`), "
                    "KEY `ix_invoice_requests_store_status` (`store_id`,`status`), "
                    "KEY `ix_invoice_requests_status` (`status`), "
                    "CONSTRAINT `fk_invoice_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`), "
                    "CONSTRAINT `fk_invoice_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`), "
                    "CONSTRAINT `fk_invoice_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电子发票开票申请'"
                )
            )

    if not _has_table(conn, "queue_tickets"):
        if sqlite:
            conn.execute(
                text(
                    "CREATE TABLE queue_tickets ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "store_id BIGINT NOT NULL REFERENCES stores(id), "
                    "customer_id BIGINT REFERENCES customers(id), "
                    "ticket_no VARCHAR(20) NOT NULL, "
                    "number INTEGER NOT NULL DEFAULT 1, "
                    "issue_date DATE NOT NULL, "
                    "people_count INTEGER NOT NULL DEFAULT 1, "
                    "status VARCHAR(20) NOT NULL DEFAULT 'WAITING', "
                    "issued_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "called_at DATETIME NULL, "
                    "seated_at DATETIME NULL, "
                    "cancelled_at DATETIME NULL, "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "CONSTRAINT uq_queue_store_date_number UNIQUE (store_id, issue_date, number))"
                )
            )
            conn.execute(text("CREATE INDEX ix_queue_store_status ON queue_tickets (store_id, status)"))
            conn.execute(text("CREATE INDEX ix_queue_customer ON queue_tickets (customer_id, issue_date)"))
            conn.execute(text("CREATE INDEX ix_queue_store_id ON queue_tickets (store_id)"))
            conn.execute(text("CREATE INDEX ix_queue_status ON queue_tickets (status)"))
        else:
            conn.execute(
                text(
                    "CREATE TABLE `queue_tickets` ("
                    "`id` bigint NOT NULL AUTO_INCREMENT, "
                    "`store_id` bigint NOT NULL, "
                    "`customer_id` bigint NULL, "
                    "`ticket_no` varchar(20) NOT NULL, "
                    "`number` int NOT NULL DEFAULT 1, "
                    "`issue_date` date NOT NULL, "
                    "`people_count` int NOT NULL DEFAULT 1, "
                    "`status` varchar(20) NOT NULL DEFAULT 'WAITING', "
                    "`issued_at` datetime DEFAULT CURRENT_TIMESTAMP, "
                    "`called_at` datetime NULL DEFAULT NULL, "
                    "`seated_at` datetime NULL DEFAULT NULL, "
                    "`cancelled_at` datetime NULL DEFAULT NULL, "
                    "`created_at` datetime DEFAULT CURRENT_TIMESTAMP, "
                    "`updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
                    "PRIMARY KEY (`id`), "
                    "UNIQUE KEY `uq_queue_store_date_number` (`store_id`,`issue_date`,`number`), "
                    "KEY `ix_queue_store_status` (`store_id`,`status`), "
                    "KEY `ix_queue_customer` (`customer_id`,`issue_date`), "
                    "KEY `ix_queue_store_id` (`store_id`), "
                    "KEY `ix_queue_status` (`status`), "
                    "CONSTRAINT `fk_queue_store` FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`), "
                    "CONSTRAINT `fk_queue_customer` FOREIGN KEY (`customer_id`) REFERENCES `customers` (`id`)"
                    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='排队取号'"
                )
            )


def upgrade() -> None:
    conn = op.get_bind()
    _add_columns(conn)
    _create_tables(conn)
    # 顾客自助退款：requested_by 放宽为可空（顾客申请时无员工经手人）
    if not conn.dialect.name == "sqlite" and _has_column(conn, "pos_refund_requests", "requested_by"):
        row = conn.execute(
            text(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pos_refund_requests' "
                "AND COLUMN_NAME = 'requested_by'"
            )
        ).scalar()
        if row == "NO":
            # 必须与 users.id 同为 bigint UNSIGNED，否则 MySQL 3780 拒绝改列
            conn.execute(
                text(
                    "ALTER TABLE `pos_refund_requests` "
                    "MODIFY COLUMN `requested_by` bigint UNSIGNED NULL DEFAULT NULL "
                    "COMMENT '申请人（顾客自助申请时为 NULL）'"
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    if _has_table(conn, "queue_tickets"):
        conn.execute(text("DROP TABLE `queue_tickets`"))
    if _has_table(conn, "invoice_requests"):
        conn.execute(text("DROP TABLE `invoice_requests`"))
    if _has_table(conn, "customer_favorite_products"):
        conn.execute(text("DROP TABLE `customer_favorite_products`"))
    for table, column in (
        ("customers", "birthday"),
        ("orders", "scheduled_pickup_at"),
        ("orders", "urged_at"),
        ("orders", "urge_count"),
    ):
        if _has_column(conn, table, column):
            conn.execute(text(f"ALTER TABLE `{table}` DROP COLUMN `{column}`"))
