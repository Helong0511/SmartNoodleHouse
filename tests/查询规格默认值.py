import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text
from app.core.database import engine


async def main():
    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT id, group_id, name, code, is_default FROM options ORDER BY group_id, sort_order")
        )
        for row in r:
            print(row)


if __name__ == "__main__":
    asyncio.run(main())
