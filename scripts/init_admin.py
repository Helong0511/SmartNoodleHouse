"""初始化超级管理员（生产空库 / 开发重置密码）"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models import Role, User


async def main():
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_INITIAL_PASSWORD") or os.environ.get("ADMIN_PASSWORD")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if user:
            if not password:
                print("已存在管理员账号。更新密码必须显式设置环境变量：")
                print("  set ADMIN_INITIAL_PASSWORD=YourStrongPass123!")
                print("  python scripts/init_admin.py")
                sys.exit(1)
            user.password_hash = hash_password(password)
            user.status = 1
            await db.commit()
            print(f"已更新管理员 {username} 的密码")
            return

        if not password:
            print("未找到管理员账号。生产部署请先执行 sql/002_seed_prod.sql，并设置强密码：")
            print("  set ADMIN_INITIAL_PASSWORD=YourStrongPass123!")
            print("  python scripts/init_admin.py")
            sys.exit(1)

        role = (
            await db.execute(select(Role).where(Role.code == "SUPER_ADMIN"))
        ).scalar_one_or_none()
        if not role:
            print("未找到 SUPER_ADMIN 角色，请先执行 sql/002_seed_prod.sql 或 sql/002_seed_test.sql")
            sys.exit(1)

        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=os.environ.get("ADMIN_DISPLAY_NAME", "系统管理员"),
                role_id=role.id,
                store_id=None,
                status=1,
            )
        )
        await db.commit()
        print(f"已创建超级管理员 {username}")


if __name__ == "__main__":
    asyncio.run(main())
