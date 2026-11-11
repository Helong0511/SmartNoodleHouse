"""限量免费抢购：活动参与门店、抢购配置/门店库存/抢购资格，订单非营收标记。

Revision ID: 0040_flash_sale_marketing
Revises: 0039_visitor_event_customer

说明：
- marketing_campaign_stores 仅记录总部活动的例外门店（included=False 为排除），
  总部活动默认全部门店参与。
- 抢购库存按 (campaign_id, store_id) 拆分，扣减必须使用条件 UPDATE。
- marketing_campaigns.code_key 解决 MySQL 中 (store_id, code) 对 NULL 不去重的问题。
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0040_flash_sale_marketing"
down_revision = "0039_visitor_event_customer"
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    if conn.dialect.name == "sqlite":
        return bool(
            conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table}
            ).scalar()
        )
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ),
            {"t": table},
        ).scalar()
    )


def _column_exists(conn, table: str, column: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _index_exists(conn, table: str, index: str) -> bool:
    if conn.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
        return any(r[1] == index for r in rows)
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
            ),
            {"t": table, "i": index},
        ).scalar()
    )


def _exec(conn, sql: str) -> None:
    conn.execute(text(sql))


def _add_code_key(conn) -> None:
    """补充 campaign code_key 并建立唯一索引（兼容历史重复数据）。"""
    if not _column_exists(conn, "marketing_campaigns", "code_key"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "ALTER TABLE marketing_campaigns ADD COLUMN code_key VARCHAR(96)")
        else:
            _exec(
                conn,
                "ALTER TABLE marketing_campaigns ADD COLUMN `code_key` varchar(96) "
                "NULL DEFAULT NULL COMMENT 'HQ:CODE 或 store_id:CODE（跨库唯一）' AFTER `code`",
            )
    if _index_exists(conn, "marketing_campaigns", "uk_mc_code_key"):
        # Once the unique index exists, never reset populated keys to their
        # non-unique natural values: doing so would violate the index on retry.
        rows = conn.execute(
            text(
                "SELECT id, store_id, code FROM marketing_campaigns "
                "WHERE code_key IS NULL OR TRIM(code_key) = '' ORDER BY id"
            )
        ).all()
        seen = {
            str(item[0])
            for item in conn.execute(
                text(
                    "SELECT code_key FROM marketing_campaigns "
                    "WHERE code_key IS NOT NULL AND TRIM(code_key) <> ''"
                )
            ).all()
        }
        for rid, store_id, code in rows:
            base = (
                f"HQ:{str(code or '').strip().upper()}"
                if store_id is None
                else f"{int(store_id)}:{str(code or '').strip().upper()}"
            )
            candidate = base[:96]
            if candidate in seen:
                suffix = f":#{int(rid)}"
                candidate = f"{base[: 96 - len(suffix)]}{suffix}"
                attempt = 1
                while candidate in seen:
                    suffix = f":#{int(rid)}:{attempt}"
                    candidate = f"{base[: 96 - len(suffix)]}{suffix}"
                    attempt += 1
            conn.execute(
                text("UPDATE marketing_campaigns SET code_key = :k WHERE id = :i"),
                {"k": candidate, "i": int(rid)},
            )
            seen.add(candidate)
        return

    if conn.dialect.name == "sqlite":
        _exec(
            conn,
            "UPDATE marketing_campaigns SET code_key = CASE "
            "WHEN store_id IS NULL THEN 'HQ:' || UPPER(code) "
            "ELSE CAST(store_id AS TEXT) || ':' || UPPER(code) END",
        )
    else:
        _exec(
            conn,
            "UPDATE marketing_campaigns SET code_key = CASE "
            "WHEN store_id IS NULL THEN CONCAT('HQ:', UPPER(code)) "
            "ELSE CONCAT(CAST(store_id AS CHAR), ':', UPPER(code)) END",
        )
    # 历史数据可能已有重复 code（MySQL NULL 不去重）：为后出现的行追加 id 后缀，保证唯一
    rows = conn.execute(text("SELECT id, code_key FROM marketing_campaigns ORDER BY id")).all()
    seen: set[str] = set()
    for rid, key in rows:
        base = (key or "HQ:").strip() or "HQ:"
        candidate = base[:96]
        if candidate in seen:
            # Keep the complete row id inside the 96-character column limit.
            # A natural HQ code may already equal the naive suffix (e.g.
            # code="ALPHA:#3" before the second "ALPHA" row is processed).
            suffix = f":#{int(rid)}"
            candidate = f"{base[: 96 - len(suffix)]}{suffix}"
            attempt = 1
            while candidate in seen:
                suffix = f":#{int(rid)}:{attempt}"
                candidate = f"{base[: 96 - len(suffix)]}{suffix}"
                attempt += 1
        if candidate != (key or ""):
            conn.execute(
                text("UPDATE marketing_campaigns SET code_key = :k WHERE id = :i"),
                {"k": candidate, "i": int(rid)},
            )
        seen.add(candidate)

    if _index_exists(conn, "marketing_campaigns", "uk_mc_code_key"):
        return
    if conn.dialect.name == "sqlite":
        _exec(
            conn,
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_mc_code_key ON marketing_campaigns (code_key)",
        )
    else:
        _exec(
            conn,
            "ALTER TABLE marketing_campaigns ADD UNIQUE INDEX uk_mc_code_key (`code_key` ASC)",
        )


_SQLITE_TABLES = {
    "marketing_campaign_stores": """
        CREATE TABLE IF NOT EXISTS marketing_campaign_stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            included BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id, store_id)
        )
    """,
    "marketing_flash_sales": """
        CREATE TABLE IF NOT EXISTS marketing_flash_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            per_customer_limit INTEGER NOT NULL DEFAULT 1,
            claim_valid_minutes INTEGER NOT NULL DEFAULT 30,
            verify_deadline_at DATETIME,
            notice VARCHAR(200),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id)
        )
    """,
    "marketing_flash_sale_stocks": """
        CREATE TABLE IF NOT EXISTS marketing_flash_sale_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            total_quantity INTEGER NOT NULL DEFAULT 0,
            remaining_quantity INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id, store_id)
        )
    """,
    "marketing_flash_sale_claims": """
        CREATE TABLE IF NOT EXISTS marketing_flash_sale_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            customer_id INTEGER NOT NULL,
            unit_no INTEGER NOT NULL DEFAULT 1,
            claim_no VARCHAR(40) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'CLAIMED',
            claimed_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            verify_deadline_at DATETIME,
            order_id INTEGER,
            verified_at DATETIME,
            verified_by INTEGER,
            client_request_id VARCHAR(64),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(campaign_id, customer_id, unit_no),
            UNIQUE(claim_no),
            UNIQUE(client_request_id)
        )
    """,
}

_SQLITE_INDEXES = {
    "marketing_campaign_stores": [
        "CREATE INDEX IF NOT EXISTS idx_mcs_store ON marketing_campaign_stores (store_id, campaign_id)",
    ],
    "marketing_flash_sales": [],
    "marketing_flash_sale_stocks": [
        "CREATE INDEX IF NOT EXISTS idx_mfss_store ON marketing_flash_sale_stocks (store_id, campaign_id)",
    ],
    "marketing_flash_sale_claims": [
        "CREATE INDEX IF NOT EXISTS idx_mfsc_customer_status ON marketing_flash_sale_claims (customer_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_mfsc_status_expire ON marketing_flash_sale_claims (status, expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_mfsc_campaign_store ON marketing_flash_sale_claims (campaign_id, store_id, status)",
    ],
}

_MYSQL_TABLES = {
    "marketing_campaign_stores": """
        CREATE TABLE IF NOT EXISTS `marketing_campaign_stores` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `campaign_id` bigint UNSIGNED NOT NULL,
          `store_id` bigint UNSIGNED NOT NULL,
          `included` tinyint(1) NOT NULL DEFAULT 0 COMMENT '0=排除该门店（总部活动默认全店参与）',
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_mcs_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
          INDEX `idx_mcs_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='活动参与门店例外（总部活动默认全店参与）'
    """,
    "marketing_flash_sales": """
        CREATE TABLE IF NOT EXISTS `marketing_flash_sales` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `campaign_id` bigint UNSIGNED NOT NULL,
          `product_id` bigint UNSIGNED NOT NULL,
          `per_customer_limit` int NOT NULL DEFAULT 1 COMMENT '每人限购数量',
          `claim_valid_minutes` int NOT NULL DEFAULT 30 COMMENT '资格有效分钟数',
          `verify_deadline_at` datetime(3) NULL DEFAULT NULL COMMENT '绝对核销截止',
          `notice` varchar(200) NULL DEFAULT NULL,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_mfs_campaign`(`campaign_id` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='限量免费抢购配置'
    """,
    "marketing_flash_sale_stocks": """
        CREATE TABLE IF NOT EXISTS `marketing_flash_sale_stocks` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `campaign_id` bigint UNSIGNED NOT NULL,
          `store_id` bigint UNSIGNED NOT NULL,
          `total_quantity` int NOT NULL DEFAULT 0,
          `remaining_quantity` int NOT NULL DEFAULT 0,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_mfss_camp_store`(`campaign_id` ASC, `store_id` ASC) USING BTREE,
          INDEX `idx_mfss_store`(`store_id` ASC, `campaign_id` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='抢购门店库存（条件 UPDATE 原子扣减）'
    """,
    "marketing_flash_sale_claims": """
        CREATE TABLE IF NOT EXISTS `marketing_flash_sale_claims` (
          `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
          `campaign_id` bigint UNSIGNED NOT NULL,
          `store_id` bigint UNSIGNED NOT NULL,
          `customer_id` bigint UNSIGNED NOT NULL,
          `unit_no` int NOT NULL DEFAULT 1 COMMENT '同一顾客第几份，配合唯一键限购',
          `claim_no` varchar(40) NOT NULL,
          `status` varchar(20) NOT NULL DEFAULT 'CLAIMED',
          `claimed_at` datetime(3) NOT NULL,
          `expires_at` datetime(3) NOT NULL,
          `verify_deadline_at` datetime(3) NULL DEFAULT NULL,
          `order_id` bigint UNSIGNED NULL DEFAULT NULL,
          `verified_at` datetime(3) NULL DEFAULT NULL,
          `verified_by` bigint UNSIGNED NULL DEFAULT NULL,
          `client_request_id` varchar(64) NULL DEFAULT NULL,
          `created_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          `updated_at` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (`id`) USING BTREE,
          UNIQUE INDEX `uk_mfsc_cust_unit`(`campaign_id` ASC, `customer_id` ASC, `unit_no` ASC) USING BTREE,
          UNIQUE INDEX `uk_mfsc_no`(`claim_no` ASC) USING BTREE,
          UNIQUE INDEX `uk_mfsc_req`(`client_request_id` ASC) USING BTREE,
          INDEX `idx_mfsc_customer_status`(`customer_id` ASC, `status` ASC) USING BTREE,
          INDEX `idx_mfsc_status_expire`(`status` ASC, `expires_at` ASC) USING BTREE,
          INDEX `idx_mfsc_campaign_store`(`campaign_id` ASC, `store_id` ASC, `status` ASC) USING BTREE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='顾客抢购资格'
    """,
}


def _add_order_flags(conn) -> None:
    if not _column_exists(conn, "orders", "non_revenue"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "ALTER TABLE orders ADD COLUMN non_revenue BOOLEAN NOT NULL DEFAULT 0")
        else:
            _exec(
                conn,
                "ALTER TABLE orders ADD COLUMN `non_revenue` tinyint(1) NOT NULL DEFAULT 0 "
                "COMMENT '营销免单：不计普通营业收入' AFTER `scheduled_pickup_at`",
            )
    if not _column_exists(conn, "orders", "marketing_campaign_id"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "ALTER TABLE orders ADD COLUMN marketing_campaign_id INTEGER")
        else:
            _exec(
                conn,
                "ALTER TABLE orders ADD COLUMN `marketing_campaign_id` bigint UNSIGNED NULL DEFAULT NULL "
                "COMMENT '来源营销活动' AFTER `non_revenue`",
            )
    if not _column_exists(conn, "orders", "flash_claim_id"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "ALTER TABLE orders ADD COLUMN flash_claim_id INTEGER")
        else:
            _exec(
                conn,
                "ALTER TABLE orders ADD COLUMN `flash_claim_id` bigint UNSIGNED NULL DEFAULT NULL "
                "COMMENT '来源抢购资格' AFTER `marketing_campaign_id`",
            )
    if not _index_exists(conn, "orders", "ix_orders_marketing_campaign"):
        if conn.dialect.name == "sqlite":
            _exec(
                conn,
                "CREATE INDEX IF NOT EXISTS ix_orders_marketing_campaign "
                "ON orders (marketing_campaign_id)",
            )
        else:
            _exec(
                conn,
                "ALTER TABLE orders ADD INDEX ix_orders_marketing_campaign (`marketing_campaign_id` ASC)",
            )


def upgrade() -> None:
    conn = op.get_bind()
    _add_code_key(conn)
    if conn.dialect.name == "sqlite":
        for name, ddl in _SQLITE_TABLES.items():
            if not _table_exists(conn, name):
                _exec(conn, ddl)
        for indexes in _SQLITE_INDEXES.values():
            for ddl in indexes:
                _exec(conn, ddl)
    else:
        for name, ddl in _MYSQL_TABLES.items():
            if not _table_exists(conn, name):
                _exec(conn, ddl)
    _add_order_flags(conn)


def downgrade() -> None:
    conn = op.get_bind()
    # 订单标记列
    if _index_exists(conn, "orders", "ix_orders_marketing_campaign"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "DROP INDEX IF EXISTS ix_orders_marketing_campaign")
        else:
            _exec(conn, "ALTER TABLE orders DROP INDEX ix_orders_marketing_campaign")
    for col in ("flash_claim_id", "marketing_campaign_id", "non_revenue"):
        if _column_exists(conn, "orders", col) and conn.dialect.name != "sqlite":
            _exec(conn, f"ALTER TABLE orders DROP COLUMN `{col}`")

    # 抢购相关表
    for table in (
        "marketing_flash_sale_claims",
        "marketing_flash_sale_stocks",
        "marketing_flash_sales",
        "marketing_campaign_stores",
    ):
        if _table_exists(conn, table):
            _exec(conn, f"DROP TABLE IF EXISTS {table}")

    # code_key 唯一索引与列
    if _index_exists(conn, "marketing_campaigns", "uk_mc_code_key"):
        if conn.dialect.name == "sqlite":
            _exec(conn, "DROP INDEX IF EXISTS uk_mc_code_key")
        else:
            _exec(conn, "ALTER TABLE marketing_campaigns DROP INDEX uk_mc_code_key")
    if _column_exists(conn, "marketing_campaigns", "code_key") and conn.dialect.name != "sqlite":
        _exec(conn, "ALTER TABLE marketing_campaigns DROP COLUMN `code_key`")
