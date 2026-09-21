"""校验审计修复是否已落地。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    url = next(
        line.split("=", 1)[1].strip()
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if line.startswith("DATABASE_URL=")
    )
    p = urlparse(re.sub(r"^mysql\+[^:]+://", "mysql://", url))
    conn = pymysql.connect(
        host=p.hostname,
        port=p.port or 3306,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        database=(p.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    ok = True
    for name in [
        "fk_orders_dining_session",
        "fk_orders_coupon",
        "fk_orders_voucher",
        "fk_users_store",
        "fk_fl_payment",
        "fk_welcome_camera",
        "fk_payments_operator",
        "fk_pl_order",
        "fk_shift_closed_by",
    ]:
        cur.execute(
            "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s",
            (name,),
        )
        present = cur.fetchone() is not None
        print(("OK" if present else "MISSING"), name)
        ok = ok and present
    for t, i in [
        ("payments", "idx_payments_no"),
        ("tables", "idx_tables_token"),
        ("financial_ledger", "idx_fl_payment"),
        ("point_ledger", "idx_pl_refund"),
    ]:
        cur.execute(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1",
            (t, i),
        )
        gone = cur.fetchone() is None
        print(("GONE" if gone else "STILL"), f"{t}.{i}")
        ok = ok and gone
    cur.execute("SHOW GRANTS")
    print("--- GRANTS ---")
    for (g,) in cur.fetchall():
        print(g)
    cur.close()
    conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
