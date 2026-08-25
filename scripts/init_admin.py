"""初始化管理员密码"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models import User


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        user = result.scalar_one_or_none()
        if user:
            user.password_hash = hash_password("admin123")
            await db.commit()
            print("Admin password reset to: admin123")
        else:
            print("Admin user not found - run SQL seed first")


if __name__ == "__main__":
    asyncio.run(main())
