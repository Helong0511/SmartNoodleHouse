import asyncio
import aiomysql
from app.core.config import get_settings


async def main():
    s = get_settings()
    # 从配置解析连接信息
    url = s.database_url.replace("mysql+aiomysql://", "")
    auth, rest = url.split("@", 1)
    user, password = auth.split(":", 1)
    host_port, db_part = rest.split("/", 1)
    host, port = host_port.split(":")
    db = db_part.split("?")[0]
    conn = await aiomysql.connect(
        host=host, port=int(port), user=user, password=password, db=db, charset="utf8mb4"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, store_no, name, latitude, longitude, status FROM stores ORDER BY id"
        )
        for r in await cur.fetchall():
            print(r)
    conn.close()


asyncio.run(main())
