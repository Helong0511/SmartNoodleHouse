"""校验数据库字符集、关键表、Alembic 版本与关键 UNIQUE/FK。

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

from app.core.database import AsyncSessionLocal, engine

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


def _charset_issues(
    session_charsets: dict[str, str],
    schema_charset: str | None,
    schema_collation: str | None,
    table_collations: list[tuple[str, str | None]],
    column_charsets: list[tuple[str, str, str | None, str | None]],
) -> list[str]:
    """数据库、连接、表和文本列必须统一使用 UTF-8/utf8mb4。"""
    issues: list[str] = []
    for variable in ("character_set_client", "character_set_connection", "character_set_results"):
        value = str(session_charsets.get(variable) or "").lower()
        if value != "utf8mb4":
            issues.append(f"connection {variable}={value or '<missing>'}, expected utf8mb4")
    if str(schema_charset or "").lower() != "utf8mb4":
        issues.append(f"database default charset={schema_charset!r}, expected utf8mb4")
    if str(schema_collation or "").lower() != "utf8mb4_unicode_ci":
        issues.append(
            f"database default collation={schema_collation!r}, expected utf8mb4_unicode_ci"
        )
    for table, collation in table_collations:
        if str(collation or "").lower() != "utf8mb4_unicode_ci":
            issues.append(
                f"table {table} collation={collation!r}, expected utf8mb4_unicode_ci"
            )
    for table, column, charset, collation in column_charsets:
        if str(charset or "").lower() != "utf8mb4":
            issues.append(
                f"column {table}.{column} charset={charset!r}, expected utf8mb4"
            )
        if str(collation or "").lower() != "utf8mb4_unicode_ci":
            issues.append(
                f"column {table}.{column} collation={collation!r}, "
                "expected utf8mb4_unicode_ci"
            )
    return issues


async def _read_charset_issues(db) -> list[str]:
    session = (
        await db.execute(
            text(
                """
                SELECT
                    @@character_set_client AS character_set_client,
                    @@character_set_connection AS character_set_connection,
                    @@character_set_results AS character_set_results,
                    s.DEFAULT_CHARACTER_SET_NAME AS schema_charset,
                    s.DEFAULT_COLLATION_NAME AS schema_collation
                FROM information_schema.SCHEMATA AS s
                WHERE s.SCHEMA_NAME = DATABASE()
                """
            )
        )
    ).mappings().one_or_none()
    if session is None:
        return ["cannot read current database character set metadata"]

    tables = (
        await db.execute(
            text(
                """
                SELECT TABLE_NAME, TABLE_COLLATION
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
                """
            )
        )
    ).all()
    columns = (
        await db.execute(
            text(
                """
                SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND CHARACTER_SET_NAME IS NOT NULL
                """
            )
        )
    ).all()
    return _charset_issues(
        {
            "character_set_client": session["character_set_client"],
            "character_set_connection": session["character_set_connection"],
            "character_set_results": session["character_set_results"],
        },
        session["schema_charset"],
        session["schema_collation"],
        [(str(row[0]), row[1]) for row in tables],
        [(str(row[0]), str(row[1]), row[2], row[3]) for row in columns],
    )


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


async def main() -> int:
    failures: list[str] = []
    async with AsyncSessionLocal() as db:
        db_name = (await db.execute(text("SELECT DATABASE()"))).scalar()
        print(f"database={db_name}")

        charset_issues = await _read_charset_issues(db)
        if charset_issues:
            for issue in charset_issues:
                print(f"[FAIL] charset {issue}")
                failures.append(f"charset: {issue}")
        else:
            print("[OK] database/session charset=utf8mb4; schema/tables/text columns=utf8mb4_unicode_ci")

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
            ("outbox_events", "idx_oe_status_retry"),
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
        return 1
    print("\n校验通过")
    return 0


async def _run_and_dispose() -> int:
    try:
        return await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_and_dispose()))
