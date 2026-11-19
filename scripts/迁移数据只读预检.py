#!/usr/bin/env python3
"""只读预检 Alembic 0003/0017 可能删除的历史重复数据。

仅接受显式 MIGRATION_PREFLIGHT_DATABASE_URL（mysql+pymysql://）。
在 MySQL REPEATABLE READ 一致性快照中强制只读事务；只执行 SELECT，最后 ROLLBACK。
不输出顾客手机号、券号、备注或支付凭据。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
from alembic.config import Config
from alembic.script import ScriptDirectory
from pymysql.cursors import DictCursor
from sqlalchemy.engine import make_url

TARGET_COUPON = "0003_p1_hardening"
TARGET_FINANCIAL = "0017_financial_ledger_idempotency"
FINANCIAL_KEYS = (
    ("SALE", "payment_id", "uq_fin_ledger_entry_payment"),
    ("REFUND", "refund_id", "uq_fin_ledger_entry_refund"),
    ("SALE", "delivery_order_id", "uq_fin_ledger_entry_delivery_order"),
    ("REFUND", "delivery_refund_id", "uq_fin_ledger_entry_delivery_refund"),
)
FINANCIAL_FACTS = (
    "store_id", "entry_type", "channel", "provider", "currency", "order_id",
    "payment_id", "refund_id", "delivery_order_id", "delivery_refund_id", "amount",
)
REQUIRED_COLUMNS = {
    "customer_coupons": {"id", "customer_id", "template_id"},
    "financial_ledger": {"id", *FINANCIAL_FACTS},
    "alembic_version": {"version_num"},
}
SAFE_SSL_QUERY_KEYS = {
    "ssl_ca", "ssl_cert", "ssl_key", "ssl_verify_cert", "ssl_verify_identity",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def summarize_financial_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """比较迁移去重将丢弃的财务业务事实。"""
    mismatches: dict[str, list[dict[str, Any]]] = {}
    for field in FINANCIAL_FACTS:
        values = [row.get(field) for row in rows]
        if any(value != values[0] for value in values[1:]):
            mismatches[field] = [
                {"id": _json_value(row.get("id")), "value": _json_value(row.get(field))}
                for row in rows
            ]
    return {
        "rows": [{key: _json_value(value) for key, value in row.items()} for row in rows],
        "fact_mismatches": mismatches,
        "facts_consistent": not mismatches,
    }


def _applied_revisions(current_revisions: list[str]) -> set[str]:
    """从当前 Alembic heads 沿 down_revision 回溯，确定目标迁移是否已经执行。"""
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    applied: set[str] = set()
    pending = list(current_revisions)
    while pending:
        revision = pending.pop()
        if revision in applied:
            continue
        node = scripts.get_revision(revision)
        if node is None:
            raise RuntimeError("数据库 revision 不存在于当前代码迁移图")
        applied.add(revision)
        parents = node.down_revision
        if parents:
            pending.extend([parents] if isinstance(parents, str) else parents)
    return applied


def _connect():
    raw_url = (os.environ.get("MIGRATION_PREFLIGHT_DATABASE_URL") or "").strip()
    if not raw_url:
        raise ValueError("必须显式设置 MIGRATION_PREFLIGHT_DATABASE_URL；不会自动读取应用 .env")
    url = make_url(raw_url)
    if url.drivername != "mysql+pymysql" or not url.host or not url.database:
        raise ValueError("仅支持 mysql+pymysql URL，且必须指定 host 和 database")
    extras = dict(url.query)
    unsupported = set(extras) - SAFE_SSL_QUERY_KEYS - {"charset"}
    if unsupported:
        raise ValueError("数据库 URL 含不支持的连接参数；请移除后重试")
    kwargs = {
        "host": url.host,
        "port": url.port or 3306,
        "user": url.username,
        "password": url.password,
        "database": url.database,
        "charset": extras.pop("charset", "utf8mb4"),
        "cursorclass": DictCursor,
        "autocommit": False,
        "connect_timeout": 10,
        "read_timeout": 60,
        "write_timeout": 10,
    }
    kwargs.update(extras)
    return pymysql.connect(**kwargs)


def _columns(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"] for row in cursor.fetchall()}


def _index_names(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["INDEX_NAME"] for row in cursor.fetchall()}


def _duplicate_groups(cursor, table: str, key_column: str, entry_type: str | None = None):
    if entry_type is None:
        cursor.execute(
            f"SELECT customer_id, template_id, COUNT(*) AS duplicate_count, "
            f"MIN(id) AS keep_id FROM {table} "
            "GROUP BY customer_id, template_id HAVING COUNT(*) > 1 "
            "ORDER BY customer_id, template_id"
        )
    else:
        cursor.execute(
            f"SELECT {key_column} AS business_key, COUNT(*) AS duplicate_count, "
            f"MIN(id) AS keep_id FROM {table} "
            "WHERE entry_type = %s AND "
            f"{key_column} IS NOT NULL GROUP BY {key_column} "
            "HAVING COUNT(*) > 1 ORDER BY " + key_column,
            (entry_type,),
        )
    return cursor.fetchall()


def _run_preflight(max_groups: int) -> dict[str, Any]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            # 一致性快照避免多个 SELECT 观察到不同时间点的数据。
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            cursor.execute("SELECT DATABASE() AS database_name, VERSION() AS server_version")
            server = cursor.fetchone()

            missing = {}
            for table, required in REQUIRED_COLUMNS.items():
                present = _columns(cursor, table)
                if not present:
                    missing[table] = sorted(required)
                elif required - present:
                    missing[table] = sorted(required - present)
            if missing:
                raise RuntimeError("数据库缺少预检所需表或字段：" + json.dumps(missing, ensure_ascii=False))

            cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
            revisions = [row["version_num"] for row in cursor.fetchall()]
            if not revisions:
                raise RuntimeError("alembic_version 没有当前 revision，无法判断迁移是否待执行")
            applied = _applied_revisions(revisions)
            coupon_indexes = _index_names(cursor, "customer_coupons")

            coupon_pending = TARGET_COUPON not in applied and "uk_cc_customer_template" not in coupon_indexes
            financial_pending = TARGET_FINANCIAL not in applied

            coupon_groups = (
                _duplicate_groups(cursor, "customer_coupons", "template_id")
                if coupon_pending else []
            )
            coupon_report = []
            for group in coupon_groups[:max_groups]:
                cursor.execute(
                    "SELECT id FROM customer_coupons "
                    "WHERE customer_id = %s AND template_id = %s ORDER BY id LIMIT 1001",
                    (group["customer_id"], group["template_id"]),
                )
                rows = cursor.fetchall()
                coupon_report.append({
                    "customer_id": group["customer_id"],
                    "template_id": group["template_id"],
                    "duplicate_count": group["duplicate_count"],
                    "keep_id": group["keep_id"],
                    "would_delete_ids": [r["id"] for r in rows if r["id"] != group["keep_id"]][:1000],
                    "row_sample_truncated": len(rows) > 1000,
                })

            financial_report = []
            financial_group_count = 0
            financial_truncated = False
            if financial_pending:
                for entry_type, key_column, index_name in FINANCIAL_KEYS:
                    groups = _duplicate_groups(cursor, "financial_ledger", key_column, entry_type)
                    financial_group_count += len(groups)
                    financial_truncated = financial_truncated or len(groups) > max_groups
                    for group in groups[:max_groups]:
                        cursor.execute(
                            "SELECT " + ", ".join(("id",) + FINANCIAL_FACTS) +
                            f" FROM financial_ledger WHERE entry_type = %s AND {key_column} = %s "
                            "ORDER BY id LIMIT 1001",
                            (entry_type, group["business_key"]),
                        )
                        rows = cursor.fetchall()
                        summary = summarize_financial_rows(rows[:1000])
                        financial_report.append({
                            "entry_type": entry_type,
                            "business_key_column": key_column,
                            "business_key": _json_value(group["business_key"]),
                            "unique_index": index_name,
                            "duplicate_count": group["duplicate_count"],
                            "keep_id": group["keep_id"],
                            "would_delete_ids": [r["id"] for r in rows if r["id"] != group["keep_id"]][:1000],
                            "row_sample_truncated": len(rows) > 1000,
                            **summary,
                        })

            truncated = (
                len(coupon_groups) > max_groups
                or financial_truncated
                or any(item["row_sample_truncated"] for item in coupon_report + financial_report)
            )
            has_duplicates = bool(coupon_groups or financial_group_count)
            return {
                "mode": "READ_ONLY",
                "database": server["database_name"],
                "server_version": server["server_version"],
                "alembic_revisions": revisions,
                "migration_targets": {
                    TARGET_COUPON: {
                        "pending": coupon_pending,
                        "reason": "not applied and unique index absent" if coupon_pending else "already applied or migration would no-op",
                    },
                    TARGET_FINANCIAL: {"pending": financial_pending},
                },
                "coupon_duplicate_groups": len(coupon_groups),
                "coupon_groups": coupon_report,
                "financial_duplicate_groups": financial_group_count,
                "financial_groups": financial_report,
                "truncated": truncated,
                "blocked": has_duplicates or truncated,
                "decision": (
                    "BLOCKED：发现迁移可能删除的重复数据；须逐组核验并由业务/财务审批"
                    if has_duplicates else
                    "BLOCKED：结果被截断，须提高 --max-groups 后重新完整预检"
                    if truncated else
                    "NO_PENDING_DESTRUCTIVE_DUPLICATES：本次一致性快照未发现待执行迁移目标重复组；仍须完成其他上线门禁"
                ),
                "safety_notes": [
                    "未读取或输出顾客手机号、券号、备注、回调原文或任何凭据",
                    "0003 仅在尚未执行且 uk_cc_customer_template 不存在时删除同 customer/template 重复券",
                    "0017 仅在尚未执行时按 entry_type + 业务键去重；fact_mismatches 列出财务业务事实差异",
                    "本报告只代表本次一致性快照，不阻止并发写入；预检后新增数据仍可能改变迁移结果",
                    "未执行任何 INSERT/UPDATE/DELETE/DDL 或 Alembic migration",
                ],
            }
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="只读统计 Alembic 0003/0017 的潜在删除数据")
    parser.add_argument("--max-groups", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.max_groups <= 10000:
        parser.error("--max-groups 必须在 1 到 10000 之间")
    try:
        report = _run_preflight(args.max_groups)
    except Exception as exc:
        print(json.dumps({
            "mode": "READ_ONLY",
            "blocked": True,
            "error": type(exc).__name__,
            "message": "预检失败或 schema 不满足要求；按 fail-closed 处理，未运行迁移",
        }, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
