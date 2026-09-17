"""外卖管理重构：营业时间/分类映射/同步告警表 + 门店绑定自动开关店字段

Revision ID: 0015_delivery_menu_ops
Revises: 0014_coupon_template_store_whitelist
Create Date: 2026-09-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_delivery_menu_ops"
down_revision = "0014_coupon_template_store_whitelist"
branch_labels = None
depends_on = None


def _col_exists(conn, table: str, column: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in rows)
    row = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).scalar()
    return int(row or 0) > 0


def _table_exists(conn, table: str) -> bool:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        row = conn.execute(
            sa.text("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        ).scalar()
    else:
        row = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    return int(row or 0) > 0


def upgrade() -> None:
    conn = op.get_bind()

    for col, ddl in (
        ("auto_sync_open", "ALTER TABLE platform_store_bindings ADD COLUMN auto_sync_open TINYINT(1) NOT NULL DEFAULT 0"),
        ("platform_open_status", "ALTER TABLE platform_store_bindings ADD COLUMN platform_open_status VARCHAR(20) NULL"),
        ("last_sync_at", "ALTER TABLE platform_store_bindings ADD COLUMN last_sync_at DATETIME NULL"),
    ):
        if not _col_exists(conn, "platform_store_bindings", col):
            if conn.dialect.name == "sqlite":
                # sqlite 不支持 TINYINT(1)，用 BOOLEAN
                if col == "auto_sync_open":
                    conn.execute(sa.text("ALTER TABLE platform_store_bindings ADD COLUMN auto_sync_open BOOLEAN NOT NULL DEFAULT 0"))
                elif col == "platform_open_status":
                    conn.execute(sa.text("ALTER TABLE platform_store_bindings ADD COLUMN platform_open_status VARCHAR(20)"))
                else:
                    conn.execute(sa.text("ALTER TABLE platform_store_bindings ADD COLUMN last_sync_at DATETIME"))
            else:
                conn.execute(sa.text(ddl))

    if not _table_exists(conn, "store_business_hours"):
        op.create_table(
            "store_business_hours",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("store_id", sa.BigInteger(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("weekday", sa.Integer(), nullable=False),
            sa.Column("slot_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("open_time", sa.Time(), nullable=False),
            sa.Column("close_time", sa.Time(), nullable=False),
            sa.Column("cross_day", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("store_id", "weekday", "slot_index", name="uk_sbh_store_day_slot"),
        )

    if not _table_exists(conn, "platform_category_maps"):
        op.create_table(
            "platform_category_maps",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("store_id", sa.BigInteger(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=True),
            sa.Column("platform", sa.String(length=20), nullable=False),
            sa.Column("local_category_id", sa.BigInteger(),
                      sa.ForeignKey("product_categories.id", ondelete="CASCADE"), nullable=False),
            sa.Column("platform_category_code", sa.String(length=64), nullable=True),
            sa.Column("platform_category_name", sa.String(length=100), nullable=True),
            sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("store_id", "platform", "local_category_id", name="uk_pcm_store_platform_cat"),
        )

    if not _table_exists(conn, "delivery_sync_alerts"):
        op.create_table(
            "delivery_sync_alerts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("store_id", sa.BigInteger(), nullable=True),
            sa.Column("platform", sa.String(length=20), nullable=True),
            sa.Column("alert_type", sa.String(length=30), nullable=False, server_default="SYNC_FAIL"),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("platform_order_id", sa.String(length=64), nullable=True),
            sa.Column("resolved", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("delivery_sync_alerts")
    op.drop_table("platform_category_maps")
    op.drop_table("store_business_hours")
    op.drop_column("platform_store_bindings", "last_sync_at")
    op.drop_column("platform_store_bindings", "platform_open_status")
    op.drop_column("platform_store_bindings", "auto_sync_open")
