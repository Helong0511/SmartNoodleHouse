"""Redis 键统一规范（智能推荐 Task 8）。

所有推荐/菜单缓存键集中在此，避免散落字符串导致失效遗漏。
前缀 snh = SmartNoodleHouse；TTL 全部由调用方显式传入并限制在常量上限内。
"""
from __future__ import annotations

import hashlib

RECO_KEY_PREFIX = "snh:reco"
RECO_TTL_SECONDS = 120  # 推荐结果最长缓存 120s（spec 要求 ≤120s）

MENU_CATALOG_PREFIX = "snh:menu"
MENU_CATALOG_TTL_SECONDS = 120


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


def menu_catalog_key(store_id: int) -> str:
    return f"{MENU_CATALOG_PREFIX}:s{int(store_id)}"


def cart_fingerprint(product_ids) -> str:
    """购物车商品集合指纹：仅与集合内容有关，与顺序无关。"""
    ids = sorted({int(pid) for pid in (product_ids or []) if pid})
    if not ids:
        return ""
    raw = ",".join(str(i) for i in ids)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
