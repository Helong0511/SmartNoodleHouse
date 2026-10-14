"""校验数据库关键表、Alembic 版本与关键 UNIQUE/FK。

用法:
  python scripts/verify_database.py
退出码: 0=通过；1=有失败项。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

REQUIRED_TABLES = [
    "roles",
    "permissions",
    "role_permissions",
    "users",
    "stores",
    "tables",
    "customers",
    "member_levels",
    "coupon_templates",
    "customer_coupons",
    "orders",
    "order_items",
    "payments",
    "payment_refunds",
    "webhook_inbox",
    "financial_ledger",
    "point_ledger",
    "print_jobs",
    "delivery_orders",
    "cashier_shifts",
    # 0025+ 日常体验 / 营销 / 社会身份 / 生命周期
    "customer_favorite_products",
    "invoice_requests",
    "queue_tickets",
    "social_identity_types",
    "customer_social_identities",
    "social_benefit_rules",
    "social_benefit_redemptions",
    "customer_segments",
    "customer_segment_members",
    "marketing_campaigns",
    "marketing_rules",
    "marketing_tasks",
    "marketing_message_logs",
    "campaign_attributions",
    "customer_lifecycle",
    "alembic_version",
]

# (table, index_name) — UNIQUE
REQUIRED_UNIQUES = [
    ("orders", "uk_idempotency"),
    ("orders", "order_no"),  # 可能是 PRIMARY/UNIQUE 列名或索引名
    ("payments", "payment_no"),
    ("payment_refunds", "refund_no"),
    ("webhook_inbox", "uk_webhook_event"),
    ("users", "username"),
    ("roles", "code"),
    ("permissions", "code"),
    ("cashier_shifts", "uk_cashier_shift_one_open"),
]

# (table, constraint_name) — CHECK
REQUIRED_CHECKS = [
    ("products", "chk_products_stock_nonneg"),
    ("products", "chk_products_price_pos"),
    ("products", "chk_products_orig_price"),
    ("store_product_stocks", "chk_sps_stock_nonneg"),
    ("options", "chk_options_extra_price"),
]

# (table, constraint_name) — FK
REQUIRED_FKS = [
    ("orders", "fk_orders_store"),
    ("orders", "fk_orders_operator"),
    ("payments", "fk_payments_order"),
    ("payment_refunds", "fk_refund_payment"),
    ("payment_refunds", "fk_refund_order"),
    ("financial_ledger", "fk_fl_store"),
    ("point_ledger", "fk_pl_customer"),
    ("print_jobs", "fk_pj_store"),
    ("print_jobs", "fk_pj_order"),
]

# (table, column)
REQUIRED_COLUMNS = [
    ("orders", "operator_id"),
    ("orders", "idempotency_key"),
    ("webhook_inbox", "attempt_count"),
    ("webhook_inbox", "last_error"),
    ("webhook_inbox", "next_retry_at"),
    ("webhook_inbox", "locked_at"),
    ("webhook_inbox", "locked_by"),
    ("payment_refunds", "retry_count"),
    ("payment_refunds", "next_retry_at"),
    ("payment_refunds", "last_error"),
    ("payment_refunds", "idempotency_key"),
    ("customer_coupons", "status_reason"),
    ("print_jobs", "status"),
    ("print_jobs", "attempt_count"),
    ("print_jobs", "next_retry_at"),
    ("print_jobs", "content"),
    ("coupon_templates", "store_id"),
    ("customers", "birthday_calendar"),
    ("customers", "birthday_locked"),
    ("products", "visibility_rule"),
    ("products", "visibility_calendar"),
    ("stores", "logo"),
]


async def _table_exists(db, name: str) -> bool:
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t
                """
            ),
            {"t": name},
        )
    ).scalar()
    return int(n or 0) > 0


async def _column_exists(db, table: str, column: str) -> bool:
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t AND COLUMN_NAME = :c
                """
            ),
            {"t": table, "c": column},
        )
    ).scalar()
    return int(n or 0) > 0


async def _index_exists(db, table: str, index: str) -> bool:
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t AND INDEX_NAME = :i
                """
            ),
            {"t": table, "i": index},
        )
    ).scalar()
    return int(n or 0) > 0


async def _unique_or_column_unique(db, table: str, name: str) -> bool:
    """索引名匹配，或单列 UNIQUE（MySQL 常把 UNIQUE(col) 的索引名设为列名）。"""
    if await _index_exists(db, table, name):
        return True
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t
                  AND COLUMN_NAME = :c
                  AND NON_UNIQUE = 0
                """
            ),
            {"t": table, "c": name},
        )
    ).scalar()
    return int(n or 0) > 0


async def _fk_exists(db, table: str, constraint: str) -> bool:
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t
                  AND CONSTRAINT_NAME = :n
                  AND CONSTRAINT_TYPE = 'FOREIGN KEY'
                """
            ),
            {"t": table, "n": constraint},
        )
    ).scalar()
    return int(n or 0) > 0


async def _check_exists(db, table: str, constraint: str) -> bool:
    n = (
        await db.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = :t
                  AND CONSTRAINT_NAME = :n
                  AND CONSTRAINT_TYPE = 'CHECK'
                """
            ),
            {"t": table, "n": constraint},
        )
    ).scalar()
    return int(n or 0) > 0


async def main() -> None:
    failures: list[str] = []
    async with AsyncSessionLocal() as db:
        db_name = (await db.execute(text("SELECT DATABASE()"))).scalar()
        print(f"database={db_name}")

        for t in REQUIRED_TABLES:
            ok = await _table_exists(db, t)
            print(f"[{'OK' if ok else 'FAIL'}] table {t}")
            if not ok:
                failures.append(f"missing table: {t}")

        if await _table_exists(db, "alembic_version"):
            ver = (await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
            print(f"[{'OK' if ver else 'FAIL'}] alembic_version={ver!r}")
            if not ver:
                failures.append("alembic_version empty")
            elif not str(ver).startswith("000"):
                print(f"[WARN] unexpected alembic revision: {ver}")
            else:
                print(f"[OK] alembic head-compatible revision={ver}")

        RECOMMENDED_INDEXES = [
            ("orders", "idx_orders_store_pay_paid"),
            ("payments", "idx_payments_status_paid"),
            ("payment_refunds", "idx_refunds_status_succ"),
            ("financial_ledger", "idx_fl_store_ch_type_created"),
            ("customer_coupons", "idx_cc_customer_status_exp"),
        ]
        for table, index in RECOMMENDED_INDEXES:
            if not await _table_exists(db, table):
                continue
            ok = await _index_exists(db, table, index)
            print(f"[{'OK' if ok else 'WARN'}] index {table}.{index}")
            if not ok:
                print(f"  hint: alembic upgrade head 或 sql/007_prod_indexes.sql")
        else:
            failures.append("missing alembic_version")

        for table, col in REQUIRED_COLUMNS:
            if not await _table_exists(db, table):
                continue
            ok = await _column_exists(db, table, col)
            print(f"[{'OK' if ok else 'FAIL'}] column {table}.{col}")
            if not ok:
                failures.append(f"missing column: {table}.{col}")

        for table, index in REQUIRED_UNIQUES:
            if not await _table_exists(db, table):
                continue
            ok = await _unique_or_column_unique(db, table, index)
            print(f"[{'OK' if ok else 'FAIL'}] unique {table}.{index}")
            if not ok:
                failures.append(f"missing unique: {table}.{index}")

        for table, fk in REQUIRED_FKS:
            if not await _table_exists(db, table):
                continue
            ok = await _fk_exists(db, table, fk)
            print(f"[{'OK' if ok else 'FAIL'}] fk {table}.{fk}")
            if not ok:
                failures.append(f"missing fk: {table}.{fk}")

        for table, chk in REQUIRED_CHECKS:
            if not await _table_exists(db, table):
                continue
            ok = await _check_exists(db, table, chk)
            print(f"[{'OK' if ok else 'FAIL'}] check {table}.{chk}")
            if not ok:
                failures.append(f"missing check: {table}.{chk}（执行 alembic upgrade head 或 sql/009_product_stock_price_checks.sql）")

    if failures:
        print("\n校验失败:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n校验通过")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
