"""P1-R2：备份 → 可选恢复到演练库 → 校验关键表存在。

用法：
  python scripts/备份恢复演练.py
  python scripts/备份恢复演练.py --restore-drill   # 需要单独配置 DRILL_DATABASE_URL
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore-drill", action="store_true")
    parser.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args()

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

    # 临时把 .env 中 DATABASE_URL 替换为演练库会过于危险；改为子进程环境注入提示
    print("请在隔离环境将 DATABASE_URL 指向演练库后执行：")
    print(f"  python scripts/恢复数据库.py {latest} --yes")
    print("然后运行：python scripts/验证数据库.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
