"""本地调试：幂等写入 shared_cart_enabled 平台默认 false。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import SystemSetting
from app.services.system_settings_service import SystemSettingsService


async def main() -> None:
    async with AsyncSessionLocal() as db:
        row = await db.scalar(
            select(SystemSetting).where(SystemSetting.setting_key == "shared_cart_enabled")
        )
        if row:
            row.setting_value = "false"
            row.description = row.description or "同桌共享购物车（默认关闭，可按门店覆盖）"
        else:
            db.add(
                SystemSetting(
                    setting_key="shared_cart_enabled",
                    setting_value="false",
                    description="同桌共享购物车（堂食多人同一份草稿车，默认关闭，可按门店覆盖）",
                )
            )
        await db.commit()
        SystemSettingsService.invalidate_cache_local()
        on = await SystemSettingsService.effective_bool(
            db, "shared_cart_enabled", False, store_id=1
        )
        print("shared_cart_enabled_effective_store1=", on)


if __name__ == "__main__":
    asyncio.run(main())
