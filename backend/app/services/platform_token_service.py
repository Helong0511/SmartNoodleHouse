"""美团 / 饿了么 OAuth Token 刷新

美团：access_token 30 天，refresh_token 35 天。建议到期前 24~48 小时刷新。
饿了么：access_token 通常 30 天，refresh_token 由平台返回。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.response import ensure_local_aware, now_local
from app.models import PlatformStoreBinding
from app.services.delivery_sign import eleme_shop_sign, meituan_sign
from app.services.secret_encryption import SecretEncryptionService

logger = logging.getLogger(__name__)


class PlatformTokenService:
    @classmethod
    async def refresh_expiring_tokens(cls, within_hours: int = 24) -> int:
        """刷新即将过期的平台门店 Token（美团 + 饿了么）。

        美团按 30 天 access_token + 35 天 refresh_token 设计：到期前 24 小时刷新，
        剩余 1 天容错窗口足够定时任务每 6 小时重试一次。

        高并发：短锁判断是否到期 → 锁外 HTTP → 短锁写回，避免持绑定行锁调外网。
        """
        from datetime import timedelta

        refreshed = 0
        deadline = now_local() + timedelta(hours=within_hours)
        binding_ids: list[int] = []
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PlatformStoreBinding.id).where(
                    PlatformStoreBinding.platform.in_(["MEITUAN", "ELEME"]),
                    PlatformStoreBinding.status == 1,
                    PlatformStoreBinding.refresh_token.isnot(None),
                )
            )
            binding_ids = [int(i) for i in result.scalars().all()]

        for bid in binding_ids:
            try:
                async with AsyncSessionLocal() as db:
                    locked = (
                        await db.execute(
                            select(PlatformStoreBinding)
                            .where(PlatformStoreBinding.id == bid)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not locked:
                        continue
                    expires = ensure_local_aware(locked.token_expires_at)
                    if expires and expires > deadline:
                        await db.commit()
                        continue
                    snap = SimpleNamespace(
                        id=int(locked.id),
                        platform=str(locked.platform or ""),
                        refresh_token=locked.refresh_token,
                        access_token=locked.access_token,
                    )
                    await db.commit()

                payload = await cls._fetch_token_payload(snap)
                if not payload:
                    continue

                async with AsyncSessionLocal() as db:
                    locked = (
                        await db.execute(
                            select(PlatformStoreBinding)
                            .where(PlatformStoreBinding.id == bid)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if not locked:
                        continue
                    cls._apply_token_payload(locked, payload)
                    await db.commit()
                    refreshed += 1
            except Exception:
                logger.exception("平台 Token 刷新异常 binding_id=%s", bid)
        if refreshed:
            logger.info("已刷新 %s 个平台门店 Token", refreshed)
        return refreshed

    @classmethod
    async def refresh_single_binding(cls, binding: PlatformStoreBinding) -> bool:
        """手动/自动刷新入口：根据 platform 分发到具体实现（可在已有会话内写回 binding）。"""
        snap = SimpleNamespace(
            id=int(getattr(binding, "id", 0) or 0),
            platform=str(binding.platform or ""),
            refresh_token=binding.refresh_token,
            access_token=binding.access_token,
        )
        payload = await cls._fetch_token_payload(snap)
        if not payload:
            return False
        cls._apply_token_payload(binding, payload)
        return True

    @classmethod
    async def _fetch_token_payload(cls, snap: SimpleNamespace) -> dict | None:
        platform = (snap.platform or "").upper()
        if platform == "MEITUAN":
            return await cls._fetch_meituan_token_payload(snap)
        if platform == "ELEME":
            return await cls._fetch_eleme_token_payload(snap)
        logger.warning("未知 platform=%s，跳过刷新", snap.platform)
        return None

    @classmethod
    def _apply_token_payload(cls, binding: PlatformStoreBinding, payload: dict) -> None:
        from datetime import timedelta

        binding.access_token = SecretEncryptionService.encrypt(payload["access_token"])
        if payload.get("refresh_token"):
            binding.refresh_token = SecretEncryptionService.encrypt(payload["refresh_token"])
        expires_in = int(payload.get("expires_in") or 86400 * 30)
        binding.token_expires_at = now_local() + timedelta(seconds=expires_in)

    @classmethod
    async def _fetch_meituan_token_payload(cls, snap: SimpleNamespace) -> dict | None:
        """美团 Token 刷新 HTTP（无写库）。"""
        settings = get_settings()
        refresh_plain = SecretEncryptionService.decrypt(snap.refresh_token)
        if not settings.meituan_app_id or not settings.meituan_app_secret or not refresh_plain:
            return None

        url = f"{settings.meituan_api_base.rstrip('/')}/api/v1/token/refresh"
        params = {
            "app_id": settings.meituan_app_id,
            "refresh_token": refresh_plain,
            "timestamp": str(int(time.time())),
        }
        params["sig"] = meituan_sign(url, params, settings.meituan_app_secret)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, data=params)
                data = resp.json()
            code = data.get("code")
            if code not in (0, "0", None) and data.get("status") not in ("success", "ok", "SUCCESS"):
                logger.warning("美团 Token 刷新失败 binding=%s: %s", snap.id, data)
                return None
            new_token = data.get("access_token") or (data.get("data") or {}).get("access_token")
            if not new_token:
                logger.warning("美团 Token 刷新返回无 access_token binding=%s: %s", snap.id, data)
                return None
            new_refresh = data.get("refresh_token") or (data.get("data") or {}).get("refresh_token")
            expires_in = int(
                data.get("expires_in") or (data.get("data") or {}).get("expires_in") or 86400 * 30
            )
            return {
                "access_token": new_token,
                "refresh_token": new_refresh,
                "expires_in": expires_in,
            }
        except Exception as e:
            logger.exception("美团 Token 刷新异常 binding=%s: %s", snap.id, e)
            return None

    @classmethod
    async def _fetch_eleme_token_payload(cls, snap: SimpleNamespace) -> dict | None:
        settings = get_settings()
        refresh_plain = SecretEncryptionService.decrypt(snap.refresh_token)
        access_plain = SecretEncryptionService.decrypt(snap.access_token)
        if not settings.eleme_app_id or not settings.eleme_app_secret or not refresh_plain:
            return None
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
                logger.warning("饿了么 Token 刷新失败 binding=%s: %s", snap.id, data)
                return None
            new_refresh = result.get("refresh_token") or result.get("refreshToken")
            expires_in = result.get("expires_in") or result.get("expiresIn") or 86400 * 30
            return {
                "access_token": new_token,
                "refresh_token": new_refresh,
                "expires_in": int(expires_in),
            }
        except Exception as e:
            logger.exception("饿了么 Token 刷新异常 binding=%s: %s", snap.id, e)
            return None
