#!/usr/bin/env python3
"""只读预检 Alembic 0003/0017 可能删除的历史重复数据。

仅接受显式 MIGRATION_PREFLIGHT_DATABASE_URL（mysql+pymysql://）。
连接后强制 START TRANSACTION READ ONLY；只执行 SELECT，最后 ROLLBACK。
不输出顾客手机号、券号、备注或支付凭据，只输出业务主键及财务核对字段。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy.engine import make_url

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
    """比较迁移去重会丢弃的财务业务事实；不把 id/created_at 当作金额事实。"""
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
        sql = (
            f"SELECT customer_id, template_id, COUNT(*) AS duplicate_count, "
            f"MIN(id) AS keep_id FROM {table} "
            "GROUP BY customer_id, template_id HAVING COUNT(*) > 1 "
            "ORDER BY customer_id, template_id"
        )
        cursor.execute(sql)
    else:
        sql = (
            f"SELECT {key_column} AS business_key, COUNT(*) AS duplicate_count, "
            f"MIN(id) AS keep_id FROM {table} "
            "WHERE entry_type = %s AND "
            f"{key_column} IS NOT NULL GROUP BY {key_column} "
            "HAVING COUNT(*) > 1 ORDER BY {key_column}"
        )
        cursor.execute(sql, (entry_type,))
    return cursor.fetchall()


def _run_preflight(max_groups: int) -> dict[str, Any]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            # 这两条语句先于业务查询执行；整个会话只读，结束时显式回滚。
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION READ ONLY")
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
            coupon_indexes = _index_names(cursor, "customer_coupons")
            financial_indexes = _index_names(cursor, "financial_ledger")

            coupon_groups = _duplicate_groups(cursor, "customer_coupons", "template_id")
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
            for entry_type, key_column, index_name in FINANCIAL_KEYS:
                groups = _duplicate_groups(cursor, "financial_ledger", key_column, entry_type)
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
                or any(len(_duplicate_groups(cursor, "financial_ledger", col, et)) > max_groups
                       for et, col, _idx in FINANCIAL_KEYS)
                or any(item["row_sample_truncated"] for item in coupon_report + financial_report)
            )
            has_duplicates = bool(coupon_groups or financial_report)
            return {
                "mode": "READ_ONLY",
                "database": server["database_name"],
                "server_version": server["server_version"],
                "alembic_revisions": revisions,
                "migration_targets": ["0003_p1_hardening", "0017_fin_ledger_idempotency"],
                "coupon_unique_index_present": "uk_cc_customer_template" in coupon_indexes,
                "coupon_duplicate_groups": len(coupon_groups),
                "coupon_groups": coupon_report,
                "financial_duplicate_groups": len(financial_report),
                "financial_groups": financial_report,
                "truncated": truncated,
                "blocked": has_duplicates or truncated,
                "decision": (
                    "BLOCKED：发现迁移可能删除的重复数据；须逐组核验并由业务/财务审批"
                    if has_duplicates else
                    "BLOCKED：结果被截断，须提高 --max-groups 后重新完整预检"
                    if truncated else
                    "NO_DUPLICATES_FOUND：本次只读快照未发现 0003/0017 目标重复组；仍须完成其他上线门禁"
                ),
                "safety_notes": [
                    "未读取或输出顾客手机号、券号、备注、回调原文或任何凭据",
                    "0003 按 customer_id/template_id 去重，但现行产品允许同模板多张券；存在组必须人工核验",
                    "0017 按 entry_type + 业务键去重；fact_mismatches 列出门店/金额/渠道等业务事实差异",
                    "本报告只代表本次只读快照，不锁定数据；预检后新增数据仍可能改变迁移结果",
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
        # 不打印连接 URL 或驱动异常详情，避免泄漏数据库信息。
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
