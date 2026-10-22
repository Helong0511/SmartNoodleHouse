"""总部 ∪ 门店双层配置：顾客端/运行时「门店覆盖总部」合并工具。

后台管理列表应保留 HQ∪店不去重；仅顾客选档与运行时解析调用本模块。
"""
from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

T = TypeVar("T")


def merge_store_over_hq(
    rows: Sequence[T],
    *,
    key_fn: Callable[[T], Hashable],
    store_id_attr: str = "store_id",
    sort_key: Callable[[T], object] | None = None,
) -> list[T]:
    """按业务键合并：先收录总部行，再用门店行覆盖同键。

    无总部对应键的门店独有行会保留；同键多条门店行以后出现的为准
    （调用方应保证同范围内业务键唯一）。
    """
    by_key: dict[Hashable, T] = {}
    for row in rows:
        if getattr(row, store_id_attr, None) is None:
            by_key[key_fn(row)] = row
    for row in rows:
        if getattr(row, store_id_attr, None) is not None:
            by_key[key_fn(row)] = row
    out = list(by_key.values())
    if sort_key is not None:
        out.sort(key=sort_key)  # type: ignore[arg-type]
    return out


def resolve_map_by_key(
    rows: Sequence[T],
    *,
    key_fn: Callable[[T], Hashable],
    store_id_attr: str = "store_id",
) -> dict[Hashable, T]:
    """同 merge_store_over_hq，但返回 key → 生效行（供同步/解析查表）。"""
    return {key_fn(r): r for r in merge_store_over_hq(rows, key_fn=key_fn, store_id_attr=store_id_attr)}


def hq_override_warning(*, key_label: str, key_desc: str, hq_name: str) -> str:
    """门店新建/保存覆盖档时的统一提示文案（不阻断保存）。"""
    return f"将覆盖顾客端/运行时总部同{key_label}（{key_desc} / 总部「{hq_name}」）"
