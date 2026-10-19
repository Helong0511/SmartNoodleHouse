"""本地调试：检查电子发票库表、开关与权限是否就绪。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services.system_settings_service import SystemSettingsService


async def main() -> None:
    async with AsyncSessionLocal() as db:
        tables = (
            await db.execute(
                text(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'invoice_requests'"
                )
            )
        ).scalar()
        print("table_invoice_requests:", bool(tables))

        row = (
            await db.execute(
                text(
                    "SELECT setting_key, setting_value FROM system_settings "
                    "WHERE setting_key = 'invoice_enabled'"
                )
            )
        ).first()
        print("platform_invoice_enabled:", dict(row._mapping) if row else None)

        store_rows = (
            await db.execute(
                text(
                    "SELECT store_id, setting_value FROM store_settings "
                    "WHERE setting_key = 'invoice_enabled' ORDER BY store_id"
                )
            )
        ).all()
        print(
            "store_overrides:",
            [dict(r._mapping) for r in store_rows] if store_rows else [],
        )

        stores = (
            await db.execute(text("SELECT id, store_no, name FROM stores WHERE status=1 ORDER BY id"))
        ).all()
        for s in stores:
            sid = int(s[0])
            on = await SystemSettingsService.effective_bool(
                db, "invoice_enabled", False, store_id=sid
            )
            print(f"effective store={sid} {s[1]}: {on}")

        perm = (
            await db.execute(
                text("SELECT id, code FROM permissions WHERE code='invoice:manage'")
            )
        ).first()
        print("permission:", dict(perm._mapping) if perm else None)

        # SUPER_ADMIN / STORE_ADMIN 是否挂了权限
        roles = (
            await db.execute(
                text(
                    "SELECT r.code, COUNT(rp.permission_id) AS cnt "
                    "FROM roles r "
                    "LEFT JOIN role_permissions rp ON rp.role_id = r.id "
                    "LEFT JOIN permissions p ON p.id = rp.permission_id AND p.code='invoice:manage' "
                    "WHERE r.code IN ('SUPER_ADMIN','STORE_ADMIN') "
                    "GROUP BY r.code"
                )
            )
        ).all()
        print("role_perm_counts:", [dict(r._mapping) for r in roles])

        # SUPER_ADMIN 通常全权限，检查 has_all 或通配
        sa = (
            await db.execute(
                text(
                    "SELECT r.code, r.name FROM roles r WHERE r.code='SUPER_ADMIN' LIMIT 1"
                )
            )
        ).first()
        print("super_admin_role:", dict(sa._mapping) if sa else None)

        cnt = (
            await db.execute(text("SELECT COUNT(*) FROM invoice_requests"))
        ).scalar()
        print("invoice_requests_count:", int(cnt or 0))

        pending = (
            await db.execute(
                text("SELECT COUNT(*) FROM invoice_requests WHERE status='PENDING'")
            )
        ).scalar()
        print("pending_count:", int(pending or 0))

        paid = (
            await db.execute(
                text("SELECT COUNT(*) FROM orders WHERE payment_status='PAID'")
            )
        ).scalar()
        print("paid_orders:", int(paid or 0))


if __name__ == "__main__":
    asyncio.run(main())
