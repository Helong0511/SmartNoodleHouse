"""芯烨云开放 API 客户端（云打印机型号，非 XP-80T 局域网版）。"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def xpyun_sign(user: str, user_key: str, timestamp: str) -> str:
    raw = f"{user}{user_key}{timestamp}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class XpyunClient:
    def __init__(
        self,
        *,
        user: str | None = None,
        user_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        settings = get_settings()
        self.user = (user or settings.xpyun_user or "").strip()
        self.user_key = (user_key or settings.xpyun_user_key or "").strip()
        base = (api_base or settings.xpyun_api_base or "").strip()
        self.api_base = base.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.user and self.user_key and self.api_base)

    def _public_params(self) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "user": self.user,
            "timestamp": ts,
            "sign": xpyun_sign(self.user, self.user_key, ts),
        }

    async def print_ticket(
        self,
        sn: str,
        content: str,
        *,
        copies: int = 1,
        cutter: int = 1,
        voice: int = 2,
        mode: int = 1,
    ) -> dict[str, Any]:
        """打印小票。cutter=1 表示由内容内 <CUT> 控制切刀（半切）。"""
        if not self.configured:
            raise RuntimeError("芯烨云未配置 XPYUN_USER / XPYUN_USER_KEY")
        sn = (sn or "").strip()
        if not sn:
            raise RuntimeError("未配置打印机 SN")
        payload = {
            **self._public_params(),
            "sn": sn,
            "content": content,
            "copies": max(1, min(int(copies), 5)),
            "cutter": int(cutter),
            "voice": int(voice),
            "mode": int(mode),
        }
        url = f"{self.api_base}/print"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"})
            resp.raise_for_status()
            data = resp.json()
        if int(data.get("code", -1)) != 0:
            logger.warning("芯烨云打印失败 sn=%s code=%s msg=%s", sn, data.get("code"), data.get("msg"))
            raise RuntimeError(str(data.get("msg") or f"芯烨云打印失败 code={data.get('code')}"))
        return data
