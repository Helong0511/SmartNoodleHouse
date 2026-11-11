"""P1-R2：备份 → 可选恢复到演练库 → 校验关键表存在。

用法：
  python scripts/备份恢复演练.py
  DRILL_DATABASE_ISOLATION_CONFIRMED=YES python scripts/备份恢复演练.py --restore-drill
  # 需要单独配置 DRILL_DATABASE_URL，并由操作员确认目标库与生产物理隔离
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import re
from urllib.parse import unquote, urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _database_identity(url: str) -> tuple[str, int, str]:
    cleaned = re.sub(r"^mysql\\+[^:]+://", "mysql://", url.strip())
    parsed = urlparse(cleaned)
    if parsed.scheme != "mysql" or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DRILL_DATABASE_URL 必须是有效的 MySQL URL")
    return (
        (parsed.hostname or "").lower().rstrip("."),
        int(parsed.port or 3306),
        unquote(parsed.path.strip("/").split("?", 1)[0]),
    )


def _isolation_confirmation_error(value: str | None) -> str | None:
    """Require an explicit operator attestation; URL comparison cannot detect DNS/proxy aliases."""
    if (value or "").strip() != "YES":
        return (
            "恢复演练前必须人工确认 DRILL_DATABASE_URL 指向与生产物理隔离的数据库；"
            "确认后设置 DRILL_DATABASE_ISOLATION_CONFIRMED=YES"
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-drill", action="store_true")
    parser.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args()

    if args.keep_days < 1:
        parser.error("--keep-days 必须大于 0")

    backup = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "备份数据库.py"), "--keep-days", str(args.keep_days)],
        cwd=str(ROOT),
        check=False,
    )
    if backup.returncode != 0:
        print("备份失败", file=sys.stderr)
        return backup.returncode

    backups = sorted((ROOT / "backups").glob("smart_noodle_house_*.sql.gz"))
    if not backups:
        print("未找到备份文件", file=sys.stderr)
        return 1
    latest = backups[-1]
    print(f"最新备份: {latest}")

    if not args.restore_drill:
        print("演练完成（仅备份）。加 --restore-drill 且配置 DRILL_DATABASE_URL 可测恢复。")
        return 0

    drill = (os.environ.get("DRILL_DATABASE_URL") or "").strip()
    if not drill:
        print("未设置 DRILL_DATABASE_URL，跳过恢复", file=sys.stderr)
        return 3

    production_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not production_url:
        env_file = ROOT / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DATABASE_URL="):
                    production_url = line.split("=", 1)[1].strip()
                    break
    if not production_url:
        print("无法读取生产 DATABASE_URL，拒绝执行恢复演练", file=sys.stderr)
        return 3
    try:
        if _database_identity(production_url) == _database_identity(drill):
            print("DRILL_DATABASE_URL 指向生产数据库，拒绝覆盖", file=sys.stderr)
            return 4
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    confirmation_error = _isolation_confirmation_error(
        os.environ.get("DRILL_DATABASE_ISOLATION_CONFIRMED")
    )
    if confirmation_error:
        print(confirmation_error, file=sys.stderr)
        return 4

    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = drill
    restore = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "恢复数据库.py"), str(latest), "--yes"],
        cwd=str(ROOT), env=child_env, check=False,
    )
    if restore.returncode != 0:
        print("演练库恢复失败", file=sys.stderr)
        return int(restore.returncode)

    verify = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "验证数据库.py")],
        cwd=str(ROOT), env=child_env, check=False,
    )
    if verify.returncode != 0:
        print("演练库结构验证失败", file=sys.stderr)
        return int(verify.returncode)

    print("备份恢复演练成功：已恢复到独立演练库并通过数据库结构检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
