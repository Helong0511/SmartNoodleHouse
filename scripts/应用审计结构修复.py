"""将审计修复对齐到已存在的本地库（幂等 ALTER）。结构真源仍为 sql/struct.sql。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pymysql


def load_database_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("未找到 DATABASE_URL")


def parse_url(url: str) -> dict:
    cleaned = re.sub(r"^mysql\+[^:]+://", "mysql://", url)
    p = urlparse(cleaned)
    return {
        "host": p.hostname or "127.0.0.1",
        "port": p.port or 3306,
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "database": (p.path or "/").lstrip("/").split("?")[0],
    }


def index_exists(cur, table: str, name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
        LIMIT 1
        """,
        (table, name),
    )
    return cur.fetchone() is not None


def fk_exists(cur, table: str, name: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s
          AND CONSTRAINT_TYPE='FOREIGN KEY' AND CONSTRAINT_NAME=%s
        LIMIT 1
        """,
        (table, name),
    )
    return cur.fetchone() is not None


def column_comment(cur, table: str, column: str) -> str | None:
    cur.execute(
        """
        SELECT COLUMN_COMMENT FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def run() -> int:
    cfg = parse_url(load_database_url())
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        autocommit=True,
    )
    cur = conn.cursor()
    actions: list[str] = []

    def exec_sql(label: str, sql: str) -> None:
        cur.execute(sql)
        actions.append(label)
        print("OK:", label)

    # ---- drop duplicate / redundant indexes ----
    drops = [
        ("financial_ledger", "idx_fl_payment"),
        ("financial_ledger", "idx_fl_refund"),
        ("payments", "idx_payments_no"),
        ("payments", "idx_payments_order"),
        ("point_ledger", "idx_pl_refund"),
        ("tables", "idx_tables_token"),
        ("customer_preferences", "idx_pref_customer"),
        ("customer_taste_profiles", "idx_ctp_customer"),
        ("store_business_hours", "idx_sbh_store"),
        ("payment_refunds", "idx_refunds_status"),
    ]
    for table, idx in drops:
        if index_exists(cur, table, idx):
            exec_sql(f"DROP INDEX {table}.{idx}", f"ALTER TABLE `{table}` DROP INDEX `{idx}`")

    # ---- column comments ----
    comment_fixes = [
        ("customer_identities", "created_at", "创建时间", False),
        ("customer_identities", "updated_at", "更新时间", True),
        ("dining_sessions", "created_at", "创建时间", False),
        ("dining_sessions", "updated_at", "更新时间", True),
    ]
    for table, col, comment, on_update in comment_fixes:
        existing = column_comment(cur, table, col) or ""
        if existing.strip():
            continue
        extra = " ON UPDATE CURRENT_TIMESTAMP(3)" if on_update else ""
        exec_sql(
            f"COMMENT {table}.{col}",
            f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` datetime(3) NOT NULL "
            f"DEFAULT CURRENT_TIMESTAMP(3){extra} COMMENT '{comment}'",
        )

    # ---- cashier_shifts defaults ----
    cur.execute(
        """
        SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='cashier_shifts'
          AND COLUMN_NAME IN ('total_cash','total_wechat','total_alipay','total_amount')
        """
    )
    for name, nullable, default, coltype in cur.fetchall():
        if nullable == "YES" or default is None:
            exec_sql(
                f"cashier_shifts.{name} NOT NULL DEFAULT 0",
                f"ALTER TABLE `cashier_shifts` MODIFY COLUMN `{name}` {coltype} "
                f"NOT NULL DEFAULT 0.00 COMMENT "
                + {
                    "total_cash": "'本班现金收款'",
                    "total_wechat": "'本班微信收款'",
                    "total_alipay": "'本班支付宝收款'",
                    "total_amount": "'本班收款合计'",
                }[name],
            )

    # ---- foreign keys ----
    fks = [
        (
            "orders",
            "fk_orders_dining_session",
            "ALTER TABLE `orders` ADD CONSTRAINT `fk_orders_dining_session` "
            "FOREIGN KEY (`dining_session_id`) REFERENCES `dining_sessions` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "orders",
            "fk_orders_coupon",
            "ALTER TABLE `orders` ADD CONSTRAINT `fk_orders_coupon` "
            "FOREIGN KEY (`customer_coupon_id`) REFERENCES `customer_coupons` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "orders",
            "fk_orders_voucher",
            "ALTER TABLE `orders` ADD CONSTRAINT `fk_orders_voucher` "
            "FOREIGN KEY (`customer_voucher_id`) REFERENCES `customer_coupons` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "users",
            "fk_users_store",
            "ALTER TABLE `users` ADD CONSTRAINT `fk_users_store` "
            "FOREIGN KEY (`store_id`) REFERENCES `stores` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "financial_ledger",
            "fk_fl_order",
            "ALTER TABLE `financial_ledger` ADD CONSTRAINT `fk_fl_order` "
            "FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "financial_ledger",
            "fk_fl_payment",
            "ALTER TABLE `financial_ledger` ADD CONSTRAINT `fk_fl_payment` "
            "FOREIGN KEY (`payment_id`) REFERENCES `payments` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "financial_ledger",
            "fk_fl_refund",
            "ALTER TABLE `financial_ledger` ADD CONSTRAINT `fk_fl_refund` "
            "FOREIGN KEY (`refund_id`) REFERENCES `payment_refunds` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "financial_ledger",
            "fk_fl_delivery_order",
            "ALTER TABLE `financial_ledger` ADD CONSTRAINT `fk_fl_delivery_order` "
            "FOREIGN KEY (`delivery_order_id`) REFERENCES `delivery_orders` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "financial_ledger",
            "fk_fl_delivery_refund",
            "ALTER TABLE `financial_ledger` ADD CONSTRAINT `fk_fl_delivery_refund` "
            "FOREIGN KEY (`delivery_refund_id`) REFERENCES `delivery_refunds` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "point_ledger",
            "fk_pl_order",
            "ALTER TABLE `point_ledger` ADD CONSTRAINT `fk_pl_order` "
            "FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "point_ledger",
            "fk_pl_refund",
            "ALTER TABLE `point_ledger` ADD CONSTRAINT `fk_pl_refund` "
            "FOREIGN KEY (`refund_id`) REFERENCES `payment_refunds` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "payments",
            "fk_payments_operator",
            "ALTER TABLE `payments` ADD CONSTRAINT `fk_payments_operator` "
            "FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "payment_refunds",
            "fk_refund_operator",
            "ALTER TABLE `payment_refunds` ADD CONSTRAINT `fk_refund_operator` "
            "FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "cashier_shifts",
            "fk_shift_closed_by",
            "ALTER TABLE `cashier_shifts` ADD CONSTRAINT `fk_shift_closed_by` "
            "FOREIGN KEY (`closed_by`) REFERENCES `users` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
        (
            "welcome_logs",
            "fk_welcome_camera",
            "ALTER TABLE `welcome_logs` ADD CONSTRAINT `fk_welcome_camera` "
            "FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) "
            "ON DELETE RESTRICT ON UPDATE RESTRICT",
        ),
    ]
    for table, name, sql in fks:
        if not fk_exists(cur, table, name):
            try:
                exec_sql(f"ADD FK {table}.{name}", sql)
            except Exception as e:
                print(f"SKIP FK {table}.{name}: {e}")

    # welcome camera index
    if not index_exists(cur, "welcome_logs", "idx_welcome_camera"):
        exec_sql(
            "ADD INDEX welcome_logs.idx_welcome_camera",
            "ALTER TABLE `welcome_logs` ADD INDEX `idx_welcome_camera`(`camera_id` ASC)",
        )

    # Harden grants on this DB only（不改口令；撤销跨库需有权限时才做）
    try:
        cur.execute(
            """
            GRANT SELECT, INSERT, UPDATE, DELETE,
              CREATE, ALTER, DROP, INDEX, REFERENCES, TRIGGER,
              CREATE TEMPORARY TABLES, EXECUTE, SHOW VIEW, LOCK TABLES
              ON `smart_noodle_house`.* TO 'remote'@'localhost'
            """
        )
        actions.append("GRANT smart_noodle_house narrowed")
        print("OK: GRANT smart_noodle_house narrowed")
        try:
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION ON `platform`.* FROM 'remote'@'localhost'")
            actions.append("REVOKE platform.*")
            print("OK: REVOKE platform.*")
        except Exception as e:
            print(f"SKIP REVOKE platform: {e}")
        cur.execute("FLUSH PRIVILEGES")
    except Exception as e:
        print(f"SKIP GRANT harden: {e}")

    print(f"\n完成，共 {len(actions)} 项变更")
    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
