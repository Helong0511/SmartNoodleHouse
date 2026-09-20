"""智能口味推荐：产品画像/顾客多维画像/行为事件/轻量评价/A-B 实验

Revision ID: 0018_taste_recommendation
Revises: 0017_fin_ledger_idempotency
Create Date: 2026-09-20
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0018_taste_recommendation"
down_revision = "0017_fin_ledger_idempotency"
branch_labels = None
depends_on = None


# ---------- 双方言存在性检查（生产 MySQL；sqlite 仅为兼容手动调用） ----------

def _table_exists(conn, table: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        row = conn.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).scalar()
        return bool(row)
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def _col_exists(conn, table: str, col: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == col for r in rows)
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
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f"PRAGMA index_list({table})").fetchall()
        return any(r[1] == name for r in rows)
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :n"
        ),
        {"t": table, "n": name},
    ).scalar()
    return bool(row)


# (table, DDL) — MySQL 生产库建表；utf8mb4，外键约束与 ORM 一致
_TABLES: tuple[tuple[str, str], ...] = (
    (
        "product_taste_profiles",
        """
        CREATE TABLE product_taste_profiles (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          product_id BIGINT UNSIGNED NOT NULL,
          store_id BIGINT UNSIGNED NOT NULL,
          dimensions JSON NULL,
          tags JSON NULL,
          source VARCHAR(10) NOT NULL DEFAULT 'AUTO',
          manual_overrides JSON NULL,
          derived_from VARCHAR(200) NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_ptp_product (product_id),
          KEY idx_ptp_store (store_id),
          CONSTRAINT fk_ptp_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
          CONSTRAINT fk_ptp_store FOREIGN KEY (store_id) REFERENCES stores(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
    (
        "customer_taste_profiles",
        """
        CREATE TABLE customer_taste_profiles (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          customer_id BIGINT UNSIGNED NOT NULL,
          dim_type VARCHAR(16) NOT NULL,
          target_key VARCHAR(100) NOT NULL,
          target_name VARCHAR(100) NULL,
          score DECIMAL(6,4) NOT NULL DEFAULT 0.0000,
          raw_weight DECIMAL(12,4) NOT NULL DEFAULT 0.0000,
          event_count INT NOT NULL DEFAULT 0,
          last_event_at DATETIME(3) NULL,
          last_computed_at DATETIME(3) NULL,
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_ctp_customer_dim_target (customer_id, dim_type, target_key),
          KEY idx_ctp_customer (customer_id),
          CONSTRAINT fk_ctp_customer FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
    (
        "customer_behavior_events",
        """
        CREATE TABLE customer_behavior_events (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          event_id VARCHAR(64) NOT NULL,
          customer_id BIGINT UNSIGNED NOT NULL,
          store_id BIGINT UNSIGNED NULL,
          event_type VARCHAR(20) NOT NULL,
          scene VARCHAR(20) NULL,
          product_id BIGINT UNSIGNED NULL,
          category_id BIGINT UNSIGNED NULL,
          ab_bucket VARCHAR(80) NULL,
          payload JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_cbe_event_id (event_id),
          KEY idx_cbe_customer_created (customer_id, created_at),
          KEY idx_cbe_store_type_created (store_id, event_type, created_at),
          KEY idx_cbe_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
    (
        "product_feedbacks",
        """
        CREATE TABLE product_feedbacks (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          order_id BIGINT UNSIGNED NOT NULL,
          product_id BIGINT UNSIGNED NOT NULL,
          customer_id BIGINT UNSIGNED NOT NULL,
          store_id BIGINT UNSIGNED NULL,
          verdicts JSON NULL,
          sentiment VARCHAR(10) NOT NULL DEFAULT 'NEUTRAL',
          remark VARCHAR(255) NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_pf_order_product_customer (order_id, product_id, customer_id),
          KEY idx_pf_customer (customer_id),
          KEY idx_pf_store (store_id),
          CONSTRAINT fk_pf_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
          CONSTRAINT fk_pf_product FOREIGN KEY (product_id) REFERENCES products(id),
          CONSTRAINT fk_pf_customer FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
    (
        "ab_experiments",
        """
        CREATE TABLE ab_experiments (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          experiment_key VARCHAR(64) NOT NULL,
          name VARCHAR(100) NOT NULL,
          status VARCHAR(10) NOT NULL DEFAULT 'DRAFT',
          traffic_percent INT NOT NULL DEFAULT 0,
          default_bucket VARCHAR(40) NOT NULL DEFAULT 'v1',
          buckets JSON NULL,
          description VARCHAR(255) NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_abe_key (experiment_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
    (
        "ab_assignments",
        """
        CREATE TABLE ab_assignments (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          experiment_key VARCHAR(64) NOT NULL,
          customer_id BIGINT UNSIGNED NOT NULL,
          bucket VARCHAR(40) NOT NULL,
          assigned_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          UNIQUE KEY uq_aba_experiment_customer (experiment_key, customer_id),
          KEY idx_aba_customer (customer_id),
          CONSTRAINT fk_aba_customer FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ),
)


def upgrade() -> None:
    conn = op.get_bind()

    # customers 个性化合规字段
    customer_cols = [
        ("recommend_opt_in", "INT NOT NULL DEFAULT 1 COMMENT '个性化推荐开关 1开 0关'"),
        ("consent_version", "VARCHAR(40) NULL COMMENT '个性化同意的隐私政策版本'"),
        ("recommend_consent_at", "DATETIME(3) NULL COMMENT '个性化同意时间'"),
    ]
    for name, ddl in customer_cols:
        if not _col_exists(conn, "customers", name):
            conn.execute(text(f"ALTER TABLE customers ADD COLUMN {name} {ddl}"))

    # product_categories 推荐差异化配置
    if not _col_exists(conn, "product_categories", "recommend_config"):
        conn.execute(
            text(
                "ALTER TABLE product_categories "
                "ADD COLUMN recommend_config JSON NULL COMMENT '推荐权重/复购窗口覆盖'"
            )
        )

    for table, ddl in _TABLES:
        if not _table_exists(conn, table):
            conn.execute(text(ddl))


def downgrade() -> None:
    conn = op.get_bind()
    for table, _ in reversed(_TABLES):
        if _table_exists(conn, table):
            conn.execute(text(f"DROP TABLE {table}"))
    if _col_exists(conn, "product_categories", "recommend_config"):
        conn.execute(text("ALTER TABLE product_categories DROP COLUMN recommend_config"))
    for col in ("recommend_consent_at", "consent_version", "recommend_opt_in"):
        if _col_exists(conn, "customers", col):
            conn.execute(text(f"ALTER TABLE customers DROP COLUMN {col}"))
