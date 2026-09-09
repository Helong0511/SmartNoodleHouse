"""饿了么等平台 OAuth Token 刷新"""
from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import ensure_local_aware, now_local
from app.models import PlatformStoreBinding
from app.services.delivery_sign import eleme_shop_sign
from app.services.secret_encryption import SecretEncryptionService

logger = logging.getLogger(__name__)


class PlatformTokenService:
    @classmethod
    async def refresh_expiring_tokens(cls, within_hours: int = 24) -> int:
        """刷新即将过期的饿了么门店 Token"""
        from datetime import timedelta

        refreshed = 0
        deadline = now_local() + timedelta(hours=within_hours)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PlatformStoreBinding).where(
                    PlatformStoreBinding.platform == "ELEME",
                    PlatformStoreBinding.status == 1,
                    PlatformStoreBinding.refresh_token.isnot(None),
                )
            )
            for binding in result.scalars():
                expires = ensure_local_aware(binding.token_expires_at)
                if expires and expires > deadline:
                    continue
                ok = await cls._refresh_eleme_binding(binding)
                if ok:
                    refreshed += 1
            await db.commit()
        if refreshed:
            logger.info("已刷新 %s 个饿了么门店 Token", refreshed)
        return refreshed

    @classmethod
    async def _refresh_eleme_binding(cls, binding: PlatformStoreBinding) -> bool:
        settings = get_settings()
        refresh_plain = SecretEncryptionService.decrypt(binding.refresh_token)
        access_plain = SecretEncryptionService.decrypt(binding.access_token)
        if not settings.eleme_app_id or not settings.eleme_app_secret or not refresh_plain:
            return False
        action = "eleme.oauth.refreshToken"
        metas = {"app_key": settings.eleme_app_id, "timestamp": int(time.time())}
        params = {"refreshToken": refresh_plain}
        payload = {
            "nop": "1.0.0",
            "id": str(uuid.uuid4()),
            "action": action,
            "token": access_plain or settings.eleme_access_token or "",
            "metas": metas,
            "params": params,
            "signature": eleme_shop_sign(
                action, access_plain or "", metas, params, settings.eleme_app_secret
            ),
        }
        url = settings.eleme_api_base.rstrip("/") + "/"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
            result = data.get("result") or {}
            if isinstance(result, str):
                result = json.loads(result)
            new_token = result.get("access_token") or result.get("accessToken")
            if not new_token:
                logger.warning("饿了么 Token 刷新失败 binding=%s: %s", binding.id, data)
                return False
            binding.access_token = SecretEncryptionService.encrypt(new_token)
            new_refresh = result.get("refresh_token") or result.get("refreshToken")
            if new_refresh:
                binding.refresh_token = SecretEncryptionService.encrypt(new_refresh)
            expires_in = result.get("expires_in") or result.get("expiresIn") or 86400 * 30
            from datetime import timedelta

            binding.token_expires_at = now_local() + timedelta(seconds=int(expires_in))
            return True
        except Exception as e:
            logger.exception("饿了么 Token 刷新异常 binding=%s: %s", binding.id, e)
            return False
