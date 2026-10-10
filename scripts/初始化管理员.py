"""初始化超级管理员（生产空库 / 开发重置密码）"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models import Role, User

# 弱口令黑名单 + 最低复杂度：长度>=10 且含字母与数字
_WEAK_PASSWORDS = {"admin123", "123456", "12345678", "password", "admin", "admin888", "111111", "000000"}


def _validate_password(password: str) -> None:
    if password.lower() in _WEAK_PASSWORDS:
        print("拒绝：密码在弱口令黑名单中，请更换强密码")
        sys.exit(1)
    if len(password) < 10 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        print("拒绝：密码至少 10 位且须同时包含字母与数字")
        sys.exit(1)


async def main():
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_INITIAL_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
    if password:
        _validate_password(password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if user:
            if not password:
                print("已存在管理员账号。更新密码必须显式设置环境变量：")
                print("  set ADMIN_INITIAL_PASSWORD=YourStrongPass123!")
                print("  python scripts/初始化管理员.py")
                sys.exit(1)
            user.password_hash = hash_password(password)
            user.status = 1
            await db.commit()
            print(f"已更新管理员 {username} 的密码")
            return

        if not password:
            print("未找到管理员账号。请先执行 sql/struct.sql + sql/data_sys.sql，并设置强密码：")
            print("  set ADMIN_INITIAL_PASSWORD=YourStrongPass123!")
            print("  python scripts/初始化管理员.py")
            sys.exit(1)

        role = (
            await db.execute(select(Role).where(Role.code == "SUPER_ADMIN"))
        ).scalar_one_or_none()
        if not role:
            print("未找到 SUPER_ADMIN 角色，请先执行 sql/data_sys.sql")
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
