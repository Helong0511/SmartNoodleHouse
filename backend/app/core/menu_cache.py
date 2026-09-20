"""门店菜单聚合读侧缓存（Task 12）。

- bundle：分类 + 在售商品一次成型的 JSON，TTL 60s 兜底，写路径主动失效。
- version：门店单调递增版本戳（INCR），客户端携带相同版本时返回 changed:false 小响应。
Redis 全部异常静默降级为未缓存，不阻塞点餐主流程。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.redis_client import get_redis

MENU_TTL_SECONDS = 60
MENU_VERSION_TTL_SECONDS = 30 * 24 * 3600


def menu_bundle_key(store_id: int) -> str:
    return f"snh:menu:bundle:{int(store_id)}"


def menu_version_key(store_id: int) -> str:
    return f"snh:menu:ver:{int(store_id)}"


async def get_menu_version(store_id: int) -> int:
    try:
        r = await get_redis()
        raw = await r.get(menu_version_key(store_id))
        return int(raw or 0)
    except Exception:
        return 0


async def bump_menu_version(store_id: int) -> int:
    """菜单变更时递增版本并删除聚合缓存；返回新版本号（Redis 不可用时返回 0）。"""
    try:
        r = await get_redis()
        version = int(await r.incr(menu_version_key(store_id)))
        if version == 1:
            await r.expire(menu_version_key(store_id), MENU_VERSION_TTL_SECONDS)
        await r.delete(menu_bundle_key(store_id))
        return version
    except Exception:
        return 0


async def get_menu_bundle(store_id: int) -> dict[str, Any] | None:
    try:
        r = await get_redis()
        raw = await r.get(menu_bundle_key(store_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def set_menu_bundle(store_id: int, bundle: dict[str, Any]) -> None:
    try:
        r = await get_redis()
        await r.set(
            menu_bundle_key(store_id),
            json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
            ex=MENU_TTL_SECONDS,
        )
    except Exception:
        pass
