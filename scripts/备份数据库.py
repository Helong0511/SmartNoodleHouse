"""备份 smart_noodle_house 到 backups/ 目录（mysqldump + gzip）。

用法：
  python scripts/备份数据库.py
  python scripts/备份数据库.py --keep-days 14
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
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "backups"


def load_database_url() -> str:
    override = (os.environ.get("DATABASE_URL") or "").strip()
    if override:
        return override
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
        "port": str(p.port or 3306),
        "user": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "database": (p.path or "/").lstrip("/").split("?")[0],
    }


def find_mysqldump() -> str:
    which = shutil.which("mysqldump")
    if which:
        return which
    candidates = [
        r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe",
        r"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    raise FileNotFoundError("未找到 mysqldump，请将 MySQL bin 加入 PATH")


def prune(keep_days: int) -> int:
    if keep_days <= 0 or not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in BACKUP_DIR.glob("smart_noodle_house_*.sql.gz"):
        try:
            # smart_noodle_house_YYYYMMDD_HHMMSS.sql.gz
            stamp = f.name.replace("smart_noodle_house_", "").replace(".sql.gz", "")
            ts = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
            if ts < cutoff:
                f.unlink()
                removed += 1
        except ValueError:
            continue
    return removed


def _run_mysqldump_to_gzip(cmd: list[str], out_path: Path, env: dict[str, str]) -> tuple[int, str]:
    """流式压缩 mysqldump 输出，避免 stdout/stderr 双 PIPE 互相阻塞。"""
    with tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            env=env,
        )
        assert proc.stdout is not None
        try:
            with gzip.open(out_path, "wb", compresslevel=6) as gz:
                while True:
                    chunk = proc.stdout.read(1024 * 256)
                    if not chunk:
                        break
                    gz.write(chunk)
            return_code = proc.wait()
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            out_path.unlink(missing_ok=True)
            raise
        finally:
            proc.stdout.close()

        stderr_file.seek(0)
        error = stderr_file.read(1024 * 1024).decode("utf-8", errors="replace")
        if return_code != 0:
            out_path.unlink(missing_ok=True)
        return int(return_code), error


def main() -> int:
    parser = argparse.ArgumentParser(description="备份业务库")
    parser.add_argument("--keep-days", type=int, default=14, help="保留天数，0=不清理")
    args = parser.parse_args()

    cfg = parse_url(load_database_url())
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(BACKUP_DIR, 0o700)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = BACKUP_DIR / f"smart_noodle_house_{stamp}.sql.gz"
    dump = find_mysqldump()

    env = os.environ.copy()
    # 避免密码出现在进程参数列表：使用 MYSQL_PWD（仅本子进程）
    env["MYSQL_PWD"] = cfg["password"]

    cmd = [
        dump,
        "-h",
        cfg["host"],
        "-P",
        cfg["port"],
        "-u",
        cfg["user"],
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--default-character-set=utf8mb4",
        "--set-gtid-purged=OFF",
        cfg["database"],
    ]
    print("备份中…", cfg["database"], "->", out_path.name)
    return_code, error = _run_mysqldump_to_gzip(cmd, out_path, env)
    if return_code != 0:
        sys.stderr.write(error)
        return return_code

    # 备份包含顾客/订单/资金等敏感数据，禁止被同机其他普通用户读取。
    if os.name == "posix":
        os.chmod(out_path, 0o600)
    size_mb = out_path.stat().st_size / 1024 / 1024
    removed = prune(args.keep_days)
    print(f"完成：{out_path} ({size_mb:.2f} MB)，清理过期备份 {removed} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
