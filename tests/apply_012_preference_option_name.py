"""Apply sql/012_preference_option_name.sql"""
import asyncio
from sqlalchemy import text
from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        r = await conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 'customer_preferences' "
                "AND column_name = 'option_name'"
            )
        )
        n = r.scalar()
        if n == 0:
            await conn.execute(
                text(
                    "ALTER TABLE customer_preferences "
                    "ADD COLUMN option_name VARCHAR(50) DEFAULT NULL "
                    "COMMENT '选项名称快照' AFTER option_code"
                )
            )
            print("added option_name")
        else:
            print("option_name already exists")


if __name__ == "__main__":
    asyncio.run(main())
