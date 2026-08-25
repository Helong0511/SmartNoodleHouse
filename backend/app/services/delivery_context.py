"""外卖平台门店凭证与 store_id 解析"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import PlatformStoreBinding


@dataclass
class PlatformCredentials:
    store_id: int | None = None
    platform_shop_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def extract_platform_shop_id(platform: str, raw: dict[str, Any], parsed: dict[str, Any]) -> str | None:
    platform = platform.upper()
    if platform == "MEITUAN":
        return str(
            raw.get("app_poi_code")
            or raw.get("wm_poi_id")
            or raw.get("poi_id")
            or parsed.get("platform_shop_id")
            or ""
        ) or None
    return str(
        raw.get("shopId")
        or raw.get("shop_id")
        or parsed.get("platform_shop_id")
        or (parsed.get("message") or {}).get("shopId")
        if isinstance(parsed.get("message"), dict)
        else None
        or ""
    ) or None


async def resolve_store_and_credentials(
    db: AsyncSession,
    platform: str,
    raw: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[int, PlatformCredentials | None]:
    """平台门店 ID → 本系统 store_id + 调用凭证"""
    shop_id = extract_platform_shop_id(platform, raw, parsed)
    settings = get_settings()

    if shop_id:
        binding = (
            await db.execute(
                select(PlatformStoreBinding).where(
                    PlatformStoreBinding.platform == platform.upper(),
                    PlatformStoreBinding.platform_shop_id == shop_id,
                    PlatformStoreBinding.status == 1,
                )
            )
        ).scalar_one_or_none()
        if binding:
            extra = {}
            if binding.extra_config:
                import json

                try:
                    extra = json.loads(binding.extra_config) if isinstance(binding.extra_config, str) else binding.extra_config
                except Exception:
                    extra = {}
            creds = PlatformCredentials(
                store_id=binding.store_id,
                platform_shop_id=binding.platform_shop_id,
                access_token=binding.access_token,
                refresh_token=binding.refresh_token,
                extra=extra,
            )
            return binding.store_id, creds

    fallback_store = int(parsed.get("store_id") or raw.get("store_id") or 1)
    global_token = settings.eleme_access_token if platform.upper() == "ELEME" else None
    global_shop = settings.eleme_shop_id if platform.upper() == "ELEME" else settings.meituan_shop_id
    if global_token or global_shop:
        return fallback_store, PlatformCredentials(
            store_id=fallback_store,
            platform_shop_id=global_shop or shop_id,
            access_token=global_token,
        )
    return fallback_store, None


async def get_credentials_for_store(
    db: AsyncSession,
    platform: str,
    store_id: int,
) -> PlatformCredentials | None:
    binding = (
        await db.execute(
            select(PlatformStoreBinding).where(
                PlatformStoreBinding.store_id == store_id,
                PlatformStoreBinding.platform == platform.upper(),
                PlatformStoreBinding.status == 1,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if not binding:
        settings = get_settings()
        if platform.upper() == "ELEME" and settings.eleme_access_token:
            return PlatformCredentials(store_id=store_id, platform_shop_id=settings.eleme_shop_id, access_token=settings.eleme_access_token)
        if platform.upper() == "MEITUAN" and settings.meituan_shop_id:
            return PlatformCredentials(store_id=store_id, platform_shop_id=settings.meituan_shop_id)
        return None
    extra = {}
    if binding.extra_config:
        import json

        try:
            extra = json.loads(binding.extra_config) if isinstance(binding.extra_config, str) else binding.extra_config
        except Exception:
            extra = {}
    return PlatformCredentials(
        store_id=binding.store_id,
        platform_shop_id=binding.platform_shop_id,
        access_token=binding.access_token,
        refresh_token=binding.refresh_token,
        extra=extra,
    )
