"""后台 worker claim 租约时间戳升级到微秒精度。

Revision ID: 0044_worker_lease_timestamp_precision
Revises: 0043_delivery_refund_fingerprint
"""
from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import mysql

revision = "0044_worker_lease_timestamp_precision"
down_revision = "0043_delivery_refund_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL DATETIME 默认精度为秒。心跳在同一秒内更新时，MySQL 默认
    # affected-rows 语义可能返回 rowcount=0，worker 会误判租约丢失并取消任务。
    # 微秒精度确保 locked_at 心跳值实际变化；SQLite 保留原生 DateTime 行为。
    if op.get_bind().dialect.name == "mysql":
        for table in ("outbox_events", "webhook_inbox"):
            op.alter_column(
                table,
                "locked_at",
                existing_type=mysql.DATETIME(),
                type_=mysql.DATETIME(fsp=6),
                existing_nullable=True,
                nullable=True,
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        for table in ("outbox_events", "webhook_inbox"):
            op.alter_column(
                table,
                "locked_at",
                existing_type=mysql.DATETIME(fsp=6),
                type_=mysql.DATETIME(),
                existing_nullable=True,
                nullable=True,
            )
