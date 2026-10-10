"""Redis 键统一规范（智能推荐 Task 8）。

所有推荐/菜单缓存键集中在此，避免散落字符串导致失效遗漏。
前缀 snh = SmartNoodleHouse；TTL 全部由调用方显式传入并限制在常量上限内。
"""
from __future__ import annotations

import hashlib
import random

RECO_KEY_PREFIX = "snh:reco"
RECO_TTL_SECONDS = 120  # 推荐结果最长缓存 120s（spec 要求 ≤120s）

MENU_CATALOG_PREFIX = "snh:menu"
MENU_CATALOG_TTL_SECONDS = 120

# P2-6/P2-7：顾客令牌刷新旋转保护 & 登录凭证一次性消费
CUSTOMER_REFRESH_ROTATION_PREFIX = "snh:cust:refresh"
LOGIN_CREDENTIAL_USED_PREFIX = "snh:authcode:used"
LOGIN_CREDENTIAL_TTL_SECONDS = 300


def ttl_with_jitter(ttl: int, *, min_ratio: float = 0.8, max_ratio: float = 1.2) -> int:
    """TTL 加随机偏移，防止缓存雪崩（同一批 key 同时过期导致 DB 瞬时压力）。"""
    return max(1, int(ttl * random.uniform(min_ratio, max_ratio)))


def reco_key(customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = "") -> str:
    """推荐结果键。fingerprint 用于 settle_addon 区分不同购物车内容。"""
    cid = int(customer_id) if customer_id else 0
    base = f"{RECO_KEY_PREFIX}:c{cid}:s{int(store_id)}:{scene}:{bucket}"
    if fingerprint:
        return f"{base}:{fingerprint}"
    return base


def reco_customer_pattern(customer_id: int) -> str:
    """画像/订单/评价变化后批量失效该顾客全部场景桶位。"""
    return f"{RECO_KEY_PREFIX}:c{int(customer_id)}:*"


def reco_rebuild_lock_key(
    customer_id: int | None, store_id: int, scene: str, bucket: str, fingerprint: str = ""
) -> str:
    """推荐结果重建互斥锁键。fingerprint 用于 settle_addon 区分不同购物车内容。"""
    cid = int(customer_id) if customer_id else 0
    base = f"{RECO_KEY_PREFIX}:lock:c{cid}:s{int(store_id)}:{scene}:{bucket}"
    return f"{base}:{fingerprint}" if fingerprint else base


def menu_rebuild_lock_key(store_id: int) -> str:
    """菜单 bundle 重建互斥锁键。"""
    return f"snh:menu:lock:{int(store_id)}"


def menu_catalog_key(store_id: int) -> str:
    return f"{MENU_CATALOG_PREFIX}:s{int(store_id)}"


def cart_fingerprint(product_ids) -> str:
    """购物车商品集合指纹：仅与集合内容有关，与顺序无关。"""
    ids = sorted({int(pid) for pid in (product_ids or []) if pid})
    if not ids:
        return ""
    raw = ",".join(str(i) for i in ids)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def customer_refresh_rotation_key(customer_id: int) -> str:
    """P2-6 刷新旋转保护键：记录该顾客已成功消费的旧令牌最大 exp。"""
    return f"{CUSTOMER_REFRESH_ROTATION_PREFIX}:{int(customer_id)}"


def login_credential_used_key(channel: str, credential: str) -> str:
    """P2-7 登录凭证一次性消费键：凭证原文不落 Redis，仅存不可逆短指纹。"""
    digest = hashlib.sha256(str(credential or "").encode("utf-8")).hexdigest()[:32]
    return f"{LOGIN_CREDENTIAL_USED_PREFIX}:{(channel or 'x').strip().lower()}:{digest}"
