"""从 backups/*.sql.gz 恢复到目标库（危险操作，仅演练/灾备）。

用法（本地调试 PowerShell）：
  python scripts/恢复数据库.py backups/smart_noodle_house_YYYYMMDD_HHMMSS.sql.gz --yes

生产（Ubuntu bash）：
  python3 scripts/恢复数据库.py /path/to/backup.sql.gz --yes
"""
from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


def load_database_url() -> str:
    override = (os.environ.get("DATABASE_URL") or "").strip()
    if override:
        return override
    env = ROOT / ".env"
    if not env.is_file():
        raise RuntimeError("未找到仓库根 .env")
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("未找到 DATABASE_URL")


def parse_url(url: str) -> dict:
    cleaned = re.sub(r"^mysql\+[^:]+://", "mysql://", url)
    p = urlparse(cleaned)
    return {
        "host": p.hostname or "127.0.0.1",
        "port": str(p.port or 3306),
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "database": (p.path or "/").lstrip("/").split("?")[0],
    }


def _decompress_backup_to_temp(backup: Path) -> Path:
    """分块解压到权限受限的临时文件，避免大备份一次性读入内存。"""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
            tmp_path = Path(tmp.name)
            with gzip.open(backup, "rb") as gz:
                shutil.copyfileobj(gz, tmp, length=1024 * 1024)
        return tmp_path
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def find_mysql() -> str:
    which = shutil.which("mysql")
    if which:
        return which
    for c in (
        r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe",
        r"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe",
    ):
        if Path(c).is_file():
            return c
    raise FileNotFoundError("未找到 mysql 客户端")


def main() -> int:
    parser = argparse.ArgumentParser(description="从 gzip SQL 备份恢复")
    parser.add_argument("backup", type=Path, help="*.sql.gz 或 *.sql")
    parser.add_argument("--yes", action="store_true", help="确认覆盖目标库")
    args = parser.parse_args()
    if not args.yes:
        print("拒绝执行：恢复会覆盖目标库，请加 --yes", file=sys.stderr)
        return 2
    backup: Path = args.backup
    if not backup.is_file():
        print(f"备份不存在: {backup}", file=sys.stderr)
        return 1

    info = parse_url(load_database_url())
    mysql = find_mysql()
    env = os.environ.copy()
    if info["password"]:
        env["MYSQL_PWD"] = info["password"]

    sql_path: Path | None = None
    try:
        if backup.suffix == ".gz" or backup.name.endswith(".sql.gz"):
            sql_path = _decompress_backup_to_temp(backup)
        else:
            sql_path = backup

        cmd = [
            mysql,
            "-h",
            info["host"],
            "-P",
            info["port"],
            "-u",
            info["user"],
            info["database"],
        ]
        with open(sql_path, "rb") as fh:
            proc = subprocess.run(cmd, stdin=fh, env=env, check=False)
        return int(proc.returncode)
    finally:
        if sql_path is not None and sql_path != backup and sql_path.exists():
            sql_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
