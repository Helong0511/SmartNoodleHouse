"""校验数据库关键表、Alembic 版本与关键 UNIQUE/FK。

用法:
  python scripts/verify_database.py
退出码: 0=通过；1=有失败项。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

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
    "payment_reconcile_batches",
    "payment_reconcile_diffs",
    "archived_order_snapshots",
    "outbox_events",
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
    ("payment_reconcile_batches", "uk_prb_channel_bill_store"),
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
    ("payment_reconcile_batches", "scope_store_id"),
    ("archived_order_snapshots", "store_id"),
    ("outbox_events", "status"),
    ("outbox_events", "attempt_count"),
    ("outbox_events", "max_attempts"),
    ("outbox_events", "locked_at"),
    ("outbox_events", "next_retry_at"),
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


def _expected_alembic_heads() -> set[str]:
    """从仓库当前 migration graph 读取真实 head，不猜测 revision 前缀。"""
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


def _alembic_heads_match(actual_revisions, expected_heads) -> bool:
    """支持 Alembic 多 head；数据库版本必须与代码 migration graph 完全一致。"""
    actual = {str(value) for value in actual_revisions if value}
    expected = {str(value) for value in expected_heads if value}
    return bool(expected) and actual == expected


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
            actual_heads = set(
                (await db.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            try:
                expected_heads = _expected_alembic_heads()
            except Exception as exc:
                failures.append(f"cannot determine repository Alembic heads: {exc}")
                expected_heads = set()
            if _alembic_heads_match(actual_heads, expected_heads):
                print(f"[OK] Alembic heads match repository: {sorted(actual_heads)}")
            else:
                print(
                    f"[FAIL] Alembic heads mismatch: database={sorted(actual_heads)}, "
                    f"repository={sorted(expected_heads)}"
                )
                failures.append(
                    f"Alembic heads mismatch: database={sorted(actual_heads)}, "
                    f"repository={sorted(expected_heads)}"
                )

        RECOMMENDED_INDEXES = [
            ("orders", "idx_orders_store_pay_paid"),
            ("payments", "idx_payments_status_paid"),
            ("payment_refunds", "idx_refunds_status_succ"),
            ("financial_ledger", "idx_fl_store_ch_type_created"),
            ("customer_coupons", "idx_cc_customer_status_exp"),
            ("payment_reconcile_batches", "ix_payment_reconcile_batches_scope_store_id"),
            ("outbox_events", "ix_oe_next_retry_at_status"),
        ]
        for table, index in RECOMMENDED_INDEXES:
            if not await _table_exists(db, table):
                continue
            ok = await _index_exists(db, table, index)
            print(f"[{'OK' if ok else 'WARN'}] index {table}.{index}")
            if not ok:
                print("  hint: alembic upgrade head 或 sql/007_prod_indexes.sql")
        if not await _table_exists(db, "alembic_version"):
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
                failures.append(
                    f"missing check: {table}.{chk}（执行 alembic upgrade head 或 sql/009_product_stock_price_checks.sql）"
                )

    if failures:
        print("\n校验失败:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\n校验通过")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
