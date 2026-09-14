"""Apply 017_payment_refund_operator migration."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        cols = (
            await conn.execute(text("SHOW COLUMNS FROM payment_refunds LIKE 'operator_id'"))
        ).fetchall()
        if not cols:
            await conn.execute(
                text(
                    "ALTER TABLE payment_refunds "
                    "ADD COLUMN operator_id BIGINT UNSIGNED DEFAULT NULL "
                    "COMMENT '发起退款的员工用户 ID' AFTER succeeded_at"
                )
            )
            print("added operator_id")
        idx = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_refunds' "
                    "AND INDEX_NAME = 'idx_refunds_operator'"
                )
            )
        ).scalar()
        if not idx:
            await conn.execute(text("ALTER TABLE payment_refunds ADD INDEX idx_refunds_operator (operator_id)"))
            print("added idx_refunds_operator")
    print("017_payment_refund_operator done")


if __name__ == "__main__":
    asyncio.run(main())
