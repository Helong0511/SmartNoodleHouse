"""一次性执行 sql/tools 下指定迁移（读项目根 .env 的 DATABASE_URL）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def load_database_url() -> str:
    text = (ROOT / ".env").read_text(encoding="utf-8")
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("DATABASE_URL="):
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("未找到 DATABASE_URL")


def connect(url: str):
    import pymysql

    raw = url.replace("mysql+aiomysql://", "mysql://").replace("mysql+asyncmy://", "mysql://")
    u = urlparse(raw)
    return pymysql.connect(
        host=u.hostname or "127.0.0.1",
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
        autocommit=True,
    )


def split_sql(text: str) -> list[str]:
    # 去掉纯注释行后按分号切；保留 PREPARE/EXECUTE 等多语句块
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("--"):
            continue
        lines.append(line)
    body = "\n".join(lines)
    parts = [p.strip() for p in body.split(";")]
    return [p for p in parts if p]


def main(files: list[str]) -> None:
    conn = connect(load_database_url())
    cur = conn.cursor()
    for rel in files:
        path = ROOT / rel
        print(f"==> {rel}")
        sql = path.read_text(encoding="utf-8")
        for stmt in split_sql(sql):
            if re.match(r"^USE\s+", stmt, re.I):
                continue
            cur.execute(stmt)
        print(f"    ok")
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customers' "
        "AND COLUMN_NAME IN ('member_status','account_status','profile_status','first_seen_at')"
    )
    print("customers identity cols:", [r[0] for r in cur.fetchall()])
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='dining_sessions'"
    )
    print("dining_sessions:", cur.fetchone()[0])
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_identities'"
    )
    print("customer_identities:", cur.fetchone()[0])
    conn.close()


if __name__ == "__main__":
    args = sys.argv[1:] or [
        "sql/016_guest_member_dining_session.sql",
        "sql/017_customer_identity_phone_security.sql",
    ]
    main(args)
