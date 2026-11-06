"""外卖平台门店凭证与 store_id 解析"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import PlatformStoreBinding
from app.services.secret_encryption import SecretEncryptionService

logger = logging.getLogger(__name__)


def _plain_token(stored: str | None) -> str | None:
    return SecretEncryptionService.decrypt(stored)


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
    # 饿了么：禁止把本地 store_id 与平台 shopId 混用
    message = parsed.get("message") if isinstance(parsed.get("message"), dict) else {}
    return str(
        raw.get("shopId")
        or raw.get("shop_id")
        or parsed.get("platform_shop_id")
        or message.get("shopId")
        or ""
    ) or None


async def resolve_store_and_credentials(
    db: AsyncSession,
    platform: str,
    raw: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[int, PlatformCredentials | None]:
    """平台门店 ID → 本系统 store_id；无绑定则拒绝，禁止 fallback 到 1。"""
    from app.core.exceptions import AppException

    shop_id = extract_platform_shop_id(platform, raw, parsed)
    settings = get_settings()

    if not shop_id:
        raise AppException(40060, "回调缺少平台门店 ID，拒绝处理")

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
            access_token=_plain_token(binding.access_token),
            refresh_token=_plain_token(binding.refresh_token),
            extra=extra,
        )
        return binding.store_id, creds

    # 单店全局配置：仅当 .env 中 platform shop_id 与回调一致，且显式配置了兜底门店时使用
    global_shop = settings.eleme_shop_id if platform.upper() == "ELEME" else settings.meituan_shop_id
    global_token = settings.eleme_access_token if platform.upper() == "ELEME" else None
    from app.services.system_settings_service import SystemSettingsService

    fallback_sid = await SystemSettingsService.effective_int(
        db,
        "delivery_env_fallback_store_id",
        int(getattr(settings, "delivery_env_fallback_store_id", 0) or 0),
    )
    if global_shop and str(global_shop) == str(shop_id) and fallback_sid > 0:
        # 禁止信任回调体里的 local_store_id（可伪造跨店）
        return fallback_sid, PlatformCredentials(
            store_id=fallback_sid,
            platform_shop_id=global_shop,
            access_token=global_token,
        )

    raise AppException(40062, f"未知平台门店，拒绝落单: {platform}/{shop_id}")


async def get_credentials_for_store(
    db: AsyncSession,
    platform: str,
    store_id: int,
) -> PlatformCredentials | None:
    """按本系统门店取平台凭证。

    默认禁止用 .env 全局 Token 冒充任意门店（多门店串单风险）。
    仅当显式配置 DELIVERY_ENV_FALLBACK_STORE_ID 且与请求门店一致时，才允许开发兜底。
    """
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
        from app.services.system_settings_service import SystemSettingsService

        fallback_sid = await SystemSettingsService.effective_int(
            db,
            "delivery_env_fallback_store_id",
            int(getattr(settings, "delivery_env_fallback_store_id", 0) or 0),
        )
        if fallback_sid <= 0 or int(store_id) != fallback_sid:
            return None
        if platform.upper() == "ELEME" and settings.eleme_access_token:
            return PlatformCredentials(
                store_id=store_id,
                platform_shop_id=settings.eleme_shop_id or None,
                access_token=settings.eleme_access_token,
            )
        if platform.upper() == "MEITUAN" and settings.meituan_shop_id:
            return PlatformCredentials(
                store_id=store_id,
                platform_shop_id=settings.meituan_shop_id,
            )
        return None
    extra = {}
    if binding.extra_config:
        import json

        try:
            extra = json.loads(binding.extra_config) if isinstance(binding.extra_config, str) else binding.extra_config
        except Exception:
            extra = {}
    credentials = PlatformCredentials(
        store_id=binding.store_id,
        platform_shop_id=binding.platform_shop_id,
        access_token=_plain_token(binding.access_token),
        refresh_token=_plain_token(binding.refresh_token),
        extra=extra,
    )
    # 不允许 Provider 在门店凭证缺失时回退到 .env 全局凭证：
    # 饿了么 token 是门店授权凭证；美团请求必须携带当前门店 shop_id。
    # 解密失败、授权被撤销或配置不完整时返回 None，由调用方 fail-closed。
    normalized_platform = platform.upper()
    if normalized_platform == "ELEME" and not credentials.access_token:
        logger.error("饿了么门店 access_token 缺失或解密失败 store_id=%s", store_id)
        return None
    if normalized_platform == "MEITUAN" and not credentials.platform_shop_id:
        logger.error("美团门店 platform_shop_id 缺失 store_id=%s", store_id)
        return None
    return credentials
