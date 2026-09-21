"""全面数据库审计：运行态、结构、索引、数据完整性、安全、备份相关指标。"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    import pymysql
except ImportError:
    print("需要 pymysql：pip install pymysql")
    raise SystemExit(1)


def load_database_url() -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("未找到 DATABASE_URL")


def parse_url(url: str) -> dict:
    # mysql+aiomysql://user:pass@host:port/db?charset=utf8mb4
    cleaned = re.sub(r"^mysql\+[^:]+://", "mysql://", url)
    p = urlparse(cleaned)
    return {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 3306,
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "database": (p.path or "/").lstrip("/").split("?")[0],
    }


def q(cur, sql: str, args=None):
    cur.execute(sql, args or ())
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def scalar(cur, sql: str, args=None):
    rows = q(cur, sql, args)
    if not rows:
        return None
    return next(iter(rows[0].values()))


def main() -> int:
    cfg = parse_url(load_database_url())
    report: dict = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "database": cfg["database"],
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
    }

    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=10,
        read_timeout=120,
    )
    cur = conn.cursor()

    # ---- 1. 运行态 / 资源 ----
    vars_needed = [
        "version",
        "version_comment",
        "hostname",
        "port",
        "datadir",
        "max_connections",
        "innodb_buffer_pool_size",
        "innodb_log_file_size",
        "innodb_flush_log_at_trx_commit",
        "sync_binlog",
        "slow_query_log",
        "slow_query_log_file",
        "long_query_time",
        "log_queries_not_using_indexes",
        "general_log",
        "log_bin",
        "binlog_format",
        "expire_logs_days",
        "binlog_expire_logs_seconds",
        "character_set_server",
        "collation_server",
        "time_zone",
        "sql_mode",
        "transaction_isolation",
        "wait_timeout",
        "interactive_timeout",
        "table_open_cache",
        "tmp_table_size",
        "max_heap_table_size",
        "join_buffer_size",
        "sort_buffer_size",
        "read_buffer_size",
        "performance_schema",
    ]
    runtime_vars = {}
    for name in vars_needed:
        try:
            runtime_vars[name] = scalar(cur, f"SELECT @@GLOBAL.{name}")
        except Exception as e:
            runtime_vars[name] = f"ERR:{e}"

    status_names = [
        "Threads_connected",
        "Threads_running",
        "Threads_created",
        "Max_used_connections",
        "Questions",
        "Slow_queries",
        "Uptime",
        "Bytes_received",
        "Bytes_sent",
        "Innodb_buffer_pool_reads",
        "Innodb_buffer_pool_read_requests",
        "Innodb_buffer_pool_pages_total",
        "Innodb_buffer_pool_pages_free",
        "Innodb_buffer_pool_pages_dirty",
        "Innodb_row_lock_waits",
        "Innodb_row_lock_time_avg",
        "Created_tmp_disk_tables",
        "Created_tmp_tables",
        "Select_full_join",
        "Select_scan",
        "Sort_merge_passes",
        "Open_tables",
        "Opened_tables",
        "Com_select",
        "Com_insert",
        "Com_update",
        "Com_delete",
        "Aborted_connects",
        "Aborted_clients",
        "Connection_errors_max_connections",
    ]
    status = {}
    for name in status_names:
        try:
            status[name] = scalar(cur, f"SHOW GLOBAL STATUS LIKE %s", (name,))
            # SHOW returns Variable_name, Value — scalar gets first; re-query properly
        except Exception:
            status[name] = None
    # Fix status: SHOW returns two columns
    status = {}
    for name in status_names:
        rows = q(cur, "SHOW GLOBAL STATUS LIKE %s", (name,))
        status[name] = rows[0]["Value"] if rows else None

    reads = int(status.get("Innodb_buffer_pool_read_requests") or 0)
    disk_reads = int(status.get("Innodb_buffer_pool_reads") or 0)
    hit_ratio = None
    if reads > 0:
        hit_ratio = round((1 - disk_reads / reads) * 100, 4)

    tmp_tables = int(status.get("Created_tmp_tables") or 0)
    tmp_disk = int(status.get("Created_tmp_disk_tables") or 0)
    tmp_disk_ratio = round(tmp_disk / tmp_tables * 100, 2) if tmp_tables else 0

    # datadir disk
    datadir = runtime_vars.get("datadir")
    disk_info = {}
    if isinstance(datadir, str) and datadir:
        try:
            # Windows path may use /
            path = datadir.replace("/", os.sep)
            usage = os.statvfs(path) if hasattr(os, "statvfs") else None
            if usage:
                total = usage.f_frsize * usage.f_blocks
                free = usage.f_frsize * usage.f_bavail
                disk_info = {
                    "datadir": path,
                    "total_gb": round(total / 1e9, 2),
                    "free_gb": round(free / 1e9, 2),
                    "used_pct": round((1 - free / total) * 100, 1) if total else None,
                }
            else:
                import shutil

                total, used, free = shutil.disk_usage(path)
                disk_info = {
                    "datadir": path,
                    "total_gb": round(total / 1e9, 2),
                    "free_gb": round(free / 1e9, 2),
                    "used_pct": round(used / total * 100, 1) if total else None,
                }
        except Exception as e:
            disk_info = {"datadir": datadir, "error": str(e)}

    # processlist
    processlist = q(
        cur,
        """
        SELECT ID, USER, HOST, DB, COMMAND, TIME, STATE,
               LEFT(INFO, 120) AS INFO
        FROM information_schema.PROCESSLIST
        ORDER BY TIME DESC
        """
    )

    report["runtime"] = {
        "variables": runtime_vars,
        "status": status,
        "buffer_pool_hit_ratio_pct": hit_ratio,
        "tmp_disk_table_ratio_pct": tmp_disk_ratio,
        "disk": disk_info,
        "processlist": processlist,
        "connections": {
            "current": int(status.get("Threads_connected") or 0),
            "running": int(status.get("Threads_running") or 0),
            "max_used": int(status.get("Max_used_connections") or 0),
            "max_allowed": int(runtime_vars.get("max_connections") or 0),
        },
    }

    # ---- 2/3/4. 表结构、字段、索引（information_schema） ----
    tables = q(
        cur,
        """
        SELECT TABLE_NAME, ENGINE, TABLE_ROWS, AVG_ROW_LENGTH, DATA_LENGTH, INDEX_LENGTH,
               DATA_FREE, AUTO_INCREMENT, TABLE_COLLATION, CREATE_OPTIONS, TABLE_COMMENT,
               UPDATE_TIME, CREATE_TIME
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """
    )
    columns = q(
        cur,
        """
        SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, DATA_TYPE,
               IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, CHARACTER_MAXIMUM_LENGTH,
               NUMERIC_PRECISION, NUMERIC_SCALE, COLLATION_NAME, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
    )
    indexes = q(
        cur,
        """
        SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME,
               CARDINALITY, INDEX_TYPE, NULLABLE
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
        """
    )
    fks = q(
        cur,
        """
        SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME,
               REFERENCED_COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
        ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
        """
    )

    # duplicate indexes (same column prefix)
    idx_map: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in indexes:
        idx_map[row["TABLE_NAME"]][row["INDEX_NAME"]].append(row["COLUMN_NAME"])

    duplicate_indexes = []
    redundant_indexes = []
    for table, inds in idx_map.items():
        items = list(inds.items())
        for i, (n1, cols1) in enumerate(items):
            for n2, cols2 in items[i + 1 :]:
                if cols1 == cols2:
                    duplicate_indexes.append({"table": table, "a": n1, "b": n2, "cols": cols1})
                elif cols1[: len(cols2)] == cols2 and n2 != "PRIMARY":
                    # cols2 is prefix of cols1 → cols2 may be redundant
                    redundant_indexes.append(
                        {
                            "table": table,
                            "kept": n1,
                            "maybe_redundant": n2,
                            "kept_cols": cols1,
                            "redundant_cols": cols2,
                        }
                    )
                elif cols2[: len(cols1)] == cols1 and n1 != "PRIMARY":
                    redundant_indexes.append(
                        {
                            "table": table,
                            "kept": n2,
                            "maybe_redundant": n1,
                            "kept_cols": cols2,
                            "redundant_cols": cols1,
                        }
                    )

    # tables without PK
    tables_with_pk = {r["TABLE_NAME"] for r in indexes if r["INDEX_NAME"] == "PRIMARY"}
    no_pk = [t["TABLE_NAME"] for t in tables if t["TABLE_NAME"] not in tables_with_pk]

    # columns without comment
    no_comment = [
        f"{r['TABLE_NAME']}.{r['COLUMN_NAME']}"
        for r in columns
        if not (r["COLUMN_COMMENT"] or "").strip()
    ]

    # nullable columns that look like required business fields
    suspicious_null = []
    for r in columns:
        name = r["COLUMN_NAME"].lower()
        if r["IS_NULLABLE"] == "YES" and any(
            k in name for k in ("status", "amount", "price", "qty", "quantity", "store_id", "order_id")
        ):
            if name.endswith("_at") or name.startswith("last_"):
                continue
            suspicious_null.append(f"{r['TABLE_NAME']}.{r['COLUMN_NAME']}:{r['COLUMN_TYPE']}")

    # text/blob without length consideration
    wide_varchars = [
        f"{r['TABLE_NAME']}.{r['COLUMN_NAME']}:{r['COLUMN_TYPE']}"
        for r in columns
        if r["DATA_TYPE"] == "varchar" and (r["CHARACTER_MAXIMUM_LENGTH"] or 0) >= 500
    ]

    # decimal checks
    weird_decimals = [
        f"{r['TABLE_NAME']}.{r['COLUMN_NAME']}:{r['COLUMN_TYPE']}"
        for r in columns
        if r["DATA_TYPE"] == "decimal"
        and r["COLUMN_NAME"].lower() in ("price", "amount", "total", "paid_amount", "refund_amount")
        and (r["NUMERIC_SCALE"] or 0) != 2
    ]

    # FK orphans sample checks — build list of FK pairs
    orphan_samples = []
    for fk in fks:
        sql = f"""
            SELECT COUNT(*) AS cnt FROM `{fk['TABLE_NAME']}` c
            LEFT JOIN `{fk['REFERENCED_TABLE_NAME']}` p
              ON c.`{fk['COLUMN_NAME']}` = p.`{fk['REFERENCED_COLUMN_NAME']}`
            WHERE c.`{fk['COLUMN_NAME']}` IS NOT NULL AND p.`{fk['REFERENCED_COLUMN_NAME']}` IS NULL
        """
        try:
            cnt = scalar(cur, sql)
            if cnt and int(cnt) > 0:
                orphan_samples.append(
                    {
                        "child": f"{fk['TABLE_NAME']}.{fk['COLUMN_NAME']}",
                        "parent": f"{fk['REFERENCED_TABLE_NAME']}.{fk['REFERENCED_COLUMN_NAME']}",
                        "orphans": int(cnt),
                    }
                )
        except Exception as e:
            orphan_samples.append(
                {
                    "child": f"{fk['TABLE_NAME']}.{fk['COLUMN_NAME']}",
                    "error": str(e),
                }
            )

    # tables without any FK in or out (except lookup-like)
    fk_tables = {f["TABLE_NAME"] for f in fks} | {f["REFERENCED_TABLE_NAME"] for f in fks}
    isolated = [t["TABLE_NAME"] for t in tables if t["TABLE_NAME"] not in fk_tables]

    total_data = sum(int(t["DATA_LENGTH"] or 0) for t in tables)
    total_index = sum(int(t["INDEX_LENGTH"] or 0) for t in tables)
    top_tables = sorted(
        tables,
        key=lambda t: int(t["DATA_LENGTH"] or 0) + int(t["INDEX_LENGTH"] or 0),
        reverse=True,
    )[:15]

    report["schema"] = {
        "table_count": len(tables),
        "column_count": len(columns),
        "index_count": len({(i["TABLE_NAME"], i["INDEX_NAME"]) for i in indexes}),
        "fk_count": len({(f["TABLE_NAME"], f["CONSTRAINT_NAME"]) for f in fks}),
        "total_data_mb": round(total_data / 1024 / 1024, 2),
        "total_index_mb": round(total_index / 1024 / 1024, 2),
        "tables_no_pk": no_pk,
        "columns_no_comment": no_comment[:80],
        "columns_no_comment_total": len(no_comment),
        "suspicious_nullable": suspicious_null[:40],
        "wide_varchars": wide_varchars,
        "weird_decimals": weird_decimals,
        "duplicate_indexes": duplicate_indexes,
        "redundant_indexes": redundant_indexes[:40],
        "isolated_tables": isolated,
        "orphan_fks": orphan_samples,
        "top_tables": [
            {
                "name": t["TABLE_NAME"],
                "rows": t["TABLE_ROWS"],
                "data_mb": round(int(t["DATA_LENGTH"] or 0) / 1024 / 1024, 3),
                "index_mb": round(int(t["INDEX_LENGTH"] or 0) / 1024 / 1024, 3),
                "engine": t["ENGINE"],
                "collation": t["TABLE_COLLATION"],
                "comment": t["TABLE_COMMENT"],
                "auto_inc": t["AUTO_INCREMENT"],
            }
            for t in top_tables
        ],
        "tables": [
            {
                "name": t["TABLE_NAME"],
                "rows": t["TABLE_ROWS"],
                "data_mb": round(int(t["DATA_LENGTH"] or 0) / 1024 / 1024, 3),
                "index_mb": round(int(t["INDEX_LENGTH"] or 0) / 1024 / 1024, 3),
                "engine": t["ENGINE"],
                "collation": t["TABLE_COLLATION"],
            }
            for t in tables
        ],
        "indexes_detail": [
            {
                "table": t,
                "index": n,
                "cols": ",".join(cols),
                "unique": all(
                    i["NON_UNIQUE"] == 0
                    for i in indexes
                    if i["TABLE_NAME"] == t and i["INDEX_NAME"] == n
                ),
            }
            for t, inds in idx_map.items()
            for n, cols in inds.items()
        ],
    }

    # ---- 5. 数据完整性抽样 ----
    integrity = {}

    def safe_count(sql: str):
        try:
            return int(scalar(cur, sql) or 0)
        except Exception as e:
            return f"ERR:{e}"

    order_cols = {c["COLUMN_NAME"] for c in columns if c["TABLE_NAME"] == "orders"}
    prod_cols = {c["COLUMN_NAME"] for c in columns if c["TABLE_NAME"] == "products"}

    checks = {
        "users_total": "SELECT COUNT(*) FROM users",
        "users_active": "SELECT COUNT(*) FROM users WHERE status = 1",
        "users_no_role": """
            SELECT COUNT(*) FROM users u
            LEFT JOIN roles r ON u.role_id = r.id
            WHERE r.id IS NULL
        """,
        "stores_total": "SELECT COUNT(*) FROM stores",
        "products_total": "SELECT COUNT(*) FROM products",
        "products_invalid_price": "SELECT COUNT(*) FROM products WHERE price <= 0",
        "products_negative_stock": "SELECT COUNT(*) FROM products WHERE stock < 0",
        "orders_total": "SELECT COUNT(*) FROM orders",
        "orders_negative_amount": "SELECT COUNT(*) FROM orders WHERE total_amount < 0",
        "order_items_orphan": """
            SELECT COUNT(*) FROM order_items oi
            LEFT JOIN orders o ON oi.order_id = o.id WHERE o.id IS NULL
        """,
        "payments_total": "SELECT COUNT(*) FROM payments",
        "payments_orphan_order": """
            SELECT COUNT(*) FROM payments p
            LEFT JOIN orders o ON p.order_id = o.id WHERE o.id IS NULL
        """,
        "customers_total": "SELECT COUNT(*) FROM customers",
        "roles_total": "SELECT COUNT(*) FROM roles",
        "permissions_total": "SELECT COUNT(*) FROM permissions",
        "system_settings_total": "SELECT COUNT(*) FROM system_settings",
        "tables_total": "SELECT COUNT(*) FROM `tables`",
        "duplicate_product_no": """
            SELECT COUNT(*) FROM (
              SELECT store_id, product_no FROM products
              GROUP BY store_id, product_no HAVING COUNT(*) > 1
            ) t
        """,
        "duplicate_username": """
            SELECT COUNT(*) FROM (
              SELECT username FROM users GROUP BY username HAVING COUNT(*) > 1
            ) t
        """,
        "paid_orders_unpaid_flag": """
            SELECT COUNT(*) FROM orders
            WHERE paid_at IS NOT NULL AND payment_status = 'UNPAID'
        """,
        "amount_mismatch_sample": """
            SELECT COUNT(*) FROM orders
            WHERE ABS(
              total_amount - (subtotal - discount_amount - coupon_discount
                - voucher_discount + packing_fee)
            ) > 0.01
        """,
    }
    for key, sql in list(checks.items()):
        integrity[key] = safe_count(sql)

    # status distribution
    if "status" in order_cols:
        integrity["order_status_dist"] = q(
            cur, "SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status ORDER BY cnt DESC"
        )
    if "status" in {c["COLUMN_NAME"] for c in columns if c["TABLE_NAME"] == "payments"}:
        integrity["payment_status_dist"] = q(
            cur, "SELECT status, COUNT(*) AS cnt FROM payments GROUP BY status ORDER BY cnt DESC"
        )

    report["integrity"] = integrity

    # ---- 6. 慢查询 / 性能 ----
    slow = {"enabled": runtime_vars.get("slow_query_log"), "long_query_time": runtime_vars.get("long_query_time")}
    try:
        # performance_schema digests
        digests = q(
            cur,
            """
            SELECT SCHEMA_NAME,
                   DIGEST_TEXT,
                   COUNT_STAR,
                   ROUND(SUM_TIMER_WAIT/1e12, 3) AS sum_sec,
                   ROUND(AVG_TIMER_WAIT/1e12, 4) AS avg_sec,
                   ROUND(MAX_TIMER_WAIT/1e12, 4) AS max_sec,
                   SUM_ROWS_EXAMINED,
                   SUM_ROWS_SENT,
                   SUM_NO_INDEX_USED,
                   SUM_NO_GOOD_INDEX_USED
            FROM performance_schema.events_statements_summary_by_digest
            WHERE SCHEMA_NAME = DATABASE() OR SCHEMA_NAME IS NULL
            ORDER BY SUM_TIMER_WAIT DESC
            LIMIT 20
            """
        )
        slow["top_digests"] = [
            {
                **d,
                "DIGEST_TEXT": (d["DIGEST_TEXT"] or "")[:200],
            }
            for d in digests
        ]
    except Exception as e:
        slow["top_digests_error"] = str(e)

    try:
        unused_idx = q(
            cur,
            """
            SELECT OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME
            FROM performance_schema.table_io_waits_summary_by_index_usage
            WHERE OBJECT_SCHEMA = DATABASE()
              AND INDEX_NAME IS NOT NULL
              AND INDEX_NAME != 'PRIMARY'
              AND COUNT_STAR = 0
            ORDER BY OBJECT_NAME, INDEX_NAME
            LIMIT 50
            """
        )
        slow["unused_indexes_ps"] = unused_idx
    except Exception as e:
        slow["unused_indexes_error"] = str(e)

    report["performance"] = slow

    # ---- 7. 安全 ----
    report["security"] = {
        "current_user": scalar(cur, "SELECT CURRENT_USER()"),
        "privilege_note": "应用账号通常无权读取 mysql.user；下列为可见范围",
    }
    try:
        users = q(
            cur,
            """
            SELECT User, Host, account_locked, password_expired,
                   password_last_changed, plugin
            FROM mysql.user
            ORDER BY User, Host
            """
        )
        grants = []
        for u in users:
            try:
                g = q(cur, "SHOW GRANTS FOR %s@%s", (u["User"], u["Host"]))
                for row in g:
                    grants.append(
                        {
                            "user": u["User"],
                            "host": u["Host"],
                            "grant": next(iter(row.values())),
                        }
                    )
            except Exception as e:
                grants.append({"user": u["User"], "host": u["Host"], "error": str(e)})
        anon = [u for u in users if u["User"] == ""]
        wild = [u for u in users if u["Host"] in ("%",)]
        report["security"].update(
            {
                "users": [
                    {
                        "user": u["User"],
                        "host": u["Host"],
                        "locked": u.get("account_locked"),
                        "pwd_expired": u.get("password_expired"),
                        "plugin": u.get("plugin"),
                        "pwd_changed": str(u.get("password_last_changed")),
                    }
                    for u in users
                ],
                "grants": grants,
                "anonymous_users": len(anon),
                "wildcard_hosts": [{"user": u["User"], "host": u["Host"]} for u in wild],
            }
        )
    except Exception as e:
        report["security"]["mysql_user_denied"] = str(e)
        try:
            schema_privs = q(
                cur,
                """
                SELECT GRANTEE, PRIVILEGE_TYPE, IS_GRANTABLE
                FROM information_schema.SCHEMA_PRIVILEGES
                WHERE TABLE_SCHEMA = DATABASE()
                ORDER BY GRANTEE, PRIVILEGE_TYPE
                """
            )
            report["security"]["schema_privileges"] = schema_privs
        except Exception as e2:
            report["security"]["schema_privileges_error"] = str(e2)
    try:
        ssl_rows = q(cur, "SHOW VARIABLES LIKE 'have_ssl'")
        report["security"]["have_ssl"] = ssl_rows[0]["Value"] if ssl_rows else None
        require_secure = q(cur, "SHOW VARIABLES LIKE 'require_secure_transport'")
        report["security"]["require_secure_transport"] = (
            require_secure[0]["Value"] if require_secure else None
        )
    except Exception as e:
        report["security"]["ssl_error"] = str(e)

    # password in struct.sql risk note handled outside

    # ---- 8. 备份 ----
    backup = {
        "log_bin": runtime_vars.get("log_bin"),
        "binlog_format": runtime_vars.get("binlog_format"),
        "binlog_expire_logs_seconds": runtime_vars.get("binlog_expire_logs_seconds"),
        "expire_logs_days": runtime_vars.get("expire_logs_days"),
    }
    try:
        backup["master_status"] = q(cur, "SHOW MASTER STATUS")
    except Exception:
        try:
            backup["master_status"] = q(cur, "SHOW BINARY LOG STATUS")
        except Exception as e:
            backup["master_status_error"] = str(e)
    try:
        backup["binary_logs"] = q(cur, "SHOW BINARY LOGS")
    except Exception as e:
        backup["binary_logs_error"] = str(e)

    # look for backup scripts/files in repo
    backup_hints = []
    for p in [
        ROOT / "scripts",
        ROOT / "docs",
        ROOT / "sql",
    ]:
        if p.exists():
            for f in p.rglob("*"):
                if f.is_file() and re.search(r"backup|备份|dump|mysqldump", f.name, re.I):
                    backup_hints.append(str(f.relative_to(ROOT)))
    backup["repo_backup_artifacts"] = backup_hints
    report["backup"] = backup

    # privileges check for current user
    try:
        report["security"]["current_grants"] = [
            next(iter(r.values())) for r in q(cur, "SHOW GRANTS")
        ]
    except Exception as e:
        report["security"]["current_grants_error"] = str(e)

    out = ROOT / "tests" / "_db_audit_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"OK wrote {out}")
    print(
        json.dumps(
            {
                "tables": report["schema"]["table_count"],
                "data_mb": report["schema"]["total_data_mb"],
                "connections": report["runtime"]["connections"],
                "hit_ratio": report["runtime"]["buffer_pool_hit_ratio_pct"],
                "slow_log": report["runtime"]["variables"].get("slow_query_log"),
                "orphans": report["schema"]["orphan_fks"],
                "no_pk": report["schema"]["tables_no_pk"],
                "integrity_keys": list(integrity.keys())[:10],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
