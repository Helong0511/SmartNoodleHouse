"""PrintJob 持久化表 print_jobs

Revision ID: 0006_print_jobs
Revises: 0005_p1_merge_print
Create Date: 2026-09-09
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0006_print_jobs"
down_revision = "0005_p1_merge_print"
branch_labels = None
depends_on = None


def _scalar(conn, sql: str, params: dict | None = None) -> int:
    row = conn.execute(text(sql), params or {}).scalar()
    return int(row or 0)


def _table_exists(conn, table: str) -> bool:
    return (
        _scalar(
            conn,
            """
            SELECT COUNT(*) FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
            """,
            {"t": table},
        )
        > 0
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "print_jobs"):
        return
    conn.execute(
        text(
            """
            CREATE TABLE print_jobs (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
              store_id BIGINT UNSIGNED NOT NULL COMMENT '门店 → stores.id',
              order_id BIGINT UNSIGNED NOT NULL COMMENT '订单 → orders.id',
              ticket_type VARCHAR(20) NOT NULL DEFAULT 'KITCHEN' COMMENT 'KITCHEN/URGE/CANCEL',
              content_hash VARCHAR(64) DEFAULT NULL COMMENT '内容 SHA-256',
              content TEXT DEFAULT NULL COMMENT '小票内容（已清控制字符，可截断）',
              status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PRINTING/SUCCESS/FAILED/RETRYING',
              attempt_count INT NOT NULL DEFAULT 0 COMMENT '已尝试次数（含当前）',
              next_retry_at DATETIME(3) DEFAULT NULL COMMENT '下次允许重试时间（指数退避）',
              last_error VARCHAR(500) DEFAULT NULL COMMENT '最近一次失败摘要',
              locked_at DATETIME(3) DEFAULT NULL COMMENT '消费者加锁时间',
              locked_by VARCHAR(64) DEFAULT NULL COMMENT '锁持有者标识（实例/worker）',
              created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
              updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
              succeeded_at DATETIME(3) DEFAULT NULL COMMENT '打印成功时间',
              INDEX idx_pj_order (order_id, ticket_type),
              INDEX idx_pj_store_status (store_id, status, created_at),
              INDEX idx_pj_retry (status, next_retry_at),
              INDEX idx_pj_locked (locked_at, locked_by),
              CONSTRAINT fk_pj_store FOREIGN KEY (store_id) REFERENCES stores(id),
              CONSTRAINT fk_pj_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='厨房小票打印任务表'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "print_jobs"):
        conn.execute(text("DROP TABLE print_jobs"))
