"""检查 MySQL 连接（本地调试小程序前可先跑：python tests/check_db.py）"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal


async def main() -> int:
    settings = get_settings()
    # 打码显示连接串
    url = settings.database_url
    if "@" in url:
        prefix, rest = url.split("@", 1)
        if "://" in prefix:
            scheme, cred = prefix.split("://", 1)
            user = cred.split(":", 1)[0]
            masked = f"{scheme}://{user}:****@{rest}"
        else:
            masked = url
    else:
        masked = url
    print("DATABASE_URL =", masked)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        print("OK: 数据库连接成功")
        return 0
    except Exception as e:
        print("FAIL:", e)
        print("\n请在 E:\\SmartNoodleHouse\\.env 设置正确的 DATABASE_URL，并确保已执行 sql/smart_noodle_house.sql。")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
