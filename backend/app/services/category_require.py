"""购买依赖：餐品级优先，分类级作默认（方案 A）"""
from __future__ import annotations

import json
from typing import Any


def normalize_id_list(raw: Any) -> list[int]:
    """解析 id 列表；非法值忽略。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    seen: set[int] = set()
    for x in raw:
        try:
            cid = int(x)
        except (TypeError, ValueError):
            continue
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


# 兼容旧名
normalize_require_category_ids = normalize_id_list


def product_has_custom_require(product: Any) -> bool:
    """餐品是否单独配置过依赖（任一字段非 NULL 即视为自定义）。"""
    return getattr(product, "require_category_ids", None) is not None or getattr(
        product, "require_product_ids", None
    ) is not None


def resolve_product_requires(
    product: Any,
    category: Any | None = None,
) -> tuple[list[int], list[int], bool]:
    """返回 (require_category_ids, require_product_ids, inherited)。

    - 餐品任一依赖字段非 NULL：用餐品自身（可为空=明确无限制）
    - 否则继承所属分类的 require_category_ids
    """
    if product_has_custom_require(product):
        return (
            normalize_id_list(getattr(product, "require_category_ids", None)),
            normalize_id_list(getattr(product, "require_product_ids", None)),
            False,
        )
    cat_ids = normalize_id_list(
        getattr(category, "require_category_ids", None) if category is not None else None
    )
    return cat_ids, [], True


def format_require_hint(
    *,
    require_category_ids: list[int],
    require_product_ids: list[int],
    category_names: dict[int, str] | None = None,
    product_names: dict[int, str] | None = None,
) -> str:
    category_names = category_names or {}
    product_names = product_names or {}
    labels: list[str] = []
    for cid in require_category_ids:
        name = category_names.get(cid) or f"分类#{cid}"
        labels.append(f"「{name}」类")
    for pid in require_product_ids:
        name = product_names.get(pid) or f"餐品#{pid}"
        labels.append(f"「{name}」")
    if not labels:
        return ""
    return "或".join(labels)
