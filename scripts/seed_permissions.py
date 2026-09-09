"""种子：权限点 + 角色权限矩阵（幂等）。

建议先执行 seed_roles.py。

用法:
  python scripts/seed_permissions.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Permission, Role, RolePermission

PERMISSIONS = [
    ("product:read", "查看商品", "product"),
    ("product:create", "创建商品", "product"),
    ("product:update", "更新商品", "product"),
    ("product:delete", "删除商品", "product"),
    ("order:read", "查看订单", "order"),
    ("order:update", "更新订单", "order"),
    ("customer:read", "查看顾客", "customer"),
    ("customer:manage", "管理会员等级", "customer"),
    ("camera:read", "查看摄像头", "camera"),
    ("camera:manage", "管理摄像头", "camera"),
    ("welcome:manage", "管理迎宾", "welcome"),
    ("payment:read", "查看支付", "payment"),
    ("payment:refund", "支付与外卖退款", "payment"),
    ("table:manage", "管理桌台", "table"),
    ("user:read", "查看员工", "user"),
    ("user:manage", "管理员工", "user"),
    ("system:manage", "系统管理", "system"),
    ("coupon:read", "查看优惠券", "coupon"),
    ("coupon:manage", "管理优惠券", "coupon"),
]

# 角色 → 权限码；SUPER_ADMIN 在代码中绑定全部权限
ROLE_PERMISSIONS: dict[str, list[str] | None] = {
    "SUPER_ADMIN": None,  # 全部
    "STORE_ADMIN": [
        "product:read",
        "product:create",
        "product:update",
        "product:delete",
        "order:read",
        "order:update",
        "customer:read",
        "customer:manage",
        "camera:read",
        "camera:manage",
        "welcome:manage",
        "payment:read",
        "payment:refund",
        "table:manage",
        "user:read",
        "user:manage",
        "coupon:read",
        "coupon:manage",
    ],
    "KITCHEN": ["order:read", "order:update"],
    "CASHIER": ["order:read", "order:update", "payment:read", "customer:read", "product:read"],
    "OPERATOR": [
        "product:read",
        "product:create",
        "product:update",
        "product:delete",
        "order:read",
        "customer:read",
        "payment:read",
    ],
}


async def main() -> None:
    async with AsyncSessionLocal() as db:
        perm_created = 0
        code_to_id: dict[str, int] = {}
        for code, name, module in PERMISSIONS:
            row = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if row:
                row.name = name
                row.module = module
                code_to_id[code] = row.id
            else:
                p = Permission(code=code, name=name, module=module)
                db.add(p)
                await db.flush()
                code_to_id[code] = p.id
                perm_created += 1

        roles = {r.code: r for r in (await db.execute(select(Role))).scalars().all()}
        missing_roles = [c for c in ROLE_PERMISSIONS if c not in roles]
        if missing_roles:
            print(f"警告: 缺少角色 {missing_roles}，请先运行 python scripts/seed_roles.py")

        rp_created = 0
        for role_code, codes in ROLE_PERMISSIONS.items():
            role = roles.get(role_code)
            if not role:
                continue
            want = list(code_to_id.keys()) if codes is None else codes
            for pcode in want:
                pid = code_to_id.get(pcode)
                if pid is None:
                    continue
                exists = (
                    await db.execute(
                        select(RolePermission).where(
                            RolePermission.role_id == role.id,
                            RolePermission.permission_id == pid,
                        )
                    )
                ).scalar_one_or_none()
                if exists:
                    continue
                db.add(RolePermission(role_id=role.id, permission_id=pid))
                rp_created += 1

        await db.commit()
        print(f"permissions: created={perm_created}, total={len(code_to_id)}; role_permissions added={rp_created}")


if __name__ == "__main__":
    asyncio.run(main())
