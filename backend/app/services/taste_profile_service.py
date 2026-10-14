"""产品口味画像：规则词典自动推导 + 店长手动微调（MANUAL 键不被 AUTO 覆盖）。

设计要点：
- 表驱动：维度等级表、风味/口感词表集中声明，扩词不改推导主流程；
  可被 system_settings 的 taste_dimension_rules/taste_tag_vocab（JSON）覆盖扩展。
- 产品绑定的规格组（如辣度四档）是顾客"可选范围"，画像取默认档（is_default），
  无默认档取中位档，代表产品典型口味；名称/描述中的风味词直接产生风味标签。
- manual_overrides 结构：
  {"dim": ["spicy", ...], "tags_add": ["卤香"], "tags_remove": ["甜"]}
  AUTO 重算时：dim 中列出的维度保留现值；tags = 自动标签(剔除 tags_remove) ∪ tags_add。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.models import (
    Option,
    OptionGroup,
    Product,
    ProductCategory,
    ProductOptionGroup,
    ProductTasteProfile,
)

logger = logging.getLogger(__name__)

# ---------- 维度元数据：key → (中文名, 最小值, 最大值) ----------
DIMENSION_META: dict[str, tuple[str, int, int]] = {
    "spicy": ("辣度", 0, 3),
    "sweetness": ("甜度", 0, 3),
    "sourness": ("酸度", 0, 3),
    "numbing": ("麻度", 0, 3),
    "temperature": ("温度", 0, 3),  # 0 冰 / 1 凉 / 2 温 / 3 热
}

# ---------- 维度等级表：维度 → [(分值, 关键词…)]，按从高到低声明，首个命中即取值 ----------
# 编码统一大写匹配；中文词小写包含匹配
_DIMENSION_LEVEL_RULES: dict[str, list[tuple[int, tuple[str, ...]]]] = {
    "spicy": [
        (3, ("EXTRA_HOT", "EXTRASPICY", "特辣", "变态辣", "爆辣", "魔鬼辣")),
        (2, ("HOT", "SPICY", "MEDIUM", "中辣", "重辣", "香辣", "麻辣", "酸辣", "辣")),
        (1, ("MILD", "LIGHT_SPICY", "微辣", "小辣", "一点点辣")),
        (0, ("NONE", "NO_SPICY", "不辣", "无辣", "免辣", "清汤", "不辣的")),
    ],
    "sweetness": [
        (3, ("FULL", "MORE", "EXTRA", "全糖", "多糖", "很甜", "特甜")),
        (2, ("NORMAL", "REGULAR", "STANDARD", "正常糖", "标准糖", "中糖", "原味甜")),
        (1, ("LESS", "LIGHT", "HALF", "少糖", "半糖", "微甜", "少甜")),
        (0, ("NONE", "ZERO", "NO_SUGAR", "无糖", "不甜", "免糖", "零糖")),
    ],
    "sourness": [
        (3, ("EXTRA_SOUR", "很酸", "特酸", "超酸")),
        (2, ("SOUR", "MEDIUM_SOUR", "中酸", "酸味", "酸香")),
        (1, ("MILD_SOUR", "酸辣", "微酸", "一点点酸", "微酸口")),
        (0, ("NO_SOUR", "不酸")),
    ],
    "numbing": [
        (3, ("EXTRA_NUMB", "特麻", "很麻", "重麻")),
        (2, ("NUMB", "MEDIUM_NUMB", "中麻", "麻辣", "麻香")),
        (1, ("MILD_NUMB", "微麻", "小麻")),
        (0, ("NO_NUMB", "不麻", "无麻")),
    ],
    "temperature": [
        (3, ("HOT", "WARM", "热", "热饮", "趁热", "热汤")),
        (2, ("NORMAL", "ROOM", "常温", "温", "温热")),
        (1, ("COOL", "CHILL", "凉", "去冰", "少冰")),
        (0, ("COLD", "ICE", "ICED", "FREEZE", "冰", "冷饮", "冰镇", "加冰", "冰饮")),
    ],
}

# 规格组 code/name → 维度（组名命中即按组内选项判定该维度）
_GROUP_DIMENSION_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spicy", ("SPICY", "HOT", "辣度", "辣")),
    ("sweetness", ("SUGAR", "SWEET", "甜度", "糖度", "糖")),
    ("sourness", ("SOUR", "酸度", "酸")),
    ("numbing", ("NUMB", "MALA", "麻度", "麻")),
    ("temperature", ("TEMP", "TEMPERATURE", "温度", "冷热", "冰热")),
)

# ---------- 受控标签词表：口感 / 风味 ----------
TEXTURE_VOCAB: tuple[str, ...] = (
    "劲道", "Q弹", "爽滑", "软糯", "绵软", "酥脆", "爽口", "细腻", "弹牙", "有嚼劲", "入口即化",
)
FLAVOR_VOCAB: tuple[str, ...] = (
    "酸辣", "麻辣", "香辣", "藤椒", "酸菜", "番茄", "蒜蓉", "酱香", "葱香", "牛肉",
    "红烧", "清汤", "浓汤", "卤香", "孜然", "咖喱", "黑椒", "柠檬", "百香果", "芝士",
    "巧克力", "草莓", "奶香", "桂花", "茉莉", "蜜桃", "芒果", "芋泥",
)

# 名称口味词直接定级（命中后覆盖规格组推断，名称是最直接的产品特征）
_NAME_DIMENSION_WORDS: dict[str, tuple[tuple[int, tuple[str, ...]], ...]] = {
    dim: rules for dim, rules in _DIMENSION_LEVEL_RULES.items()
}

_TAG_MAX_LEN = 12
_MANUAL_TAG_MAX_LEN = 12


def _norm(s: Any) -> str:
    return str(s or "").strip()


def _hit_level(text: str, rules: list[tuple[int, tuple[str, ...]]]) -> int | None:
    """收集全部命中词，按"最具体的词"（最长匹配）定级。

    例："微辣牛肉小面"同时命中 1 级「微辣」(2字) 与 2 级泛词「辣」(1字)，取微辣=1；
    英文编码同理（EXTRA_HOT 长于 HOT）。
    """
    t = text or ""
    t_upper = t.upper()
    t_compact = t_upper.replace(" ", "")
    best: tuple[int, int] | None = None  # (词长, 级别)
    for level, words in rules:
        for w in words:
            if not w:
                continue
            if w.isascii():
                hit = (len(w) >= 3 and w in t_compact) or (w in t_upper)
            else:
                hit = w in t
            if hit and (best is None or len(w) > best[0]):
                best = (len(w), level)
    return best[1] if best else None


def _group_dimension(group_name: str, group_code: str) -> str | None:
    blob = f"{group_code or ''} {group_name or ''}".upper()
    for dim, hints in _GROUP_DIMENSION_HINTS:
        for h in hints:
            if h.isascii():
                if h.upper() in blob.replace(" ", ""):
                    return dim
            elif h in (group_name or "") or h.upper() in blob:
                return dim
    return None


def derive_dimensions_and_tags(
    *,
    product_name: str,
    description: str | None = None,
    category_name: str | None = None,
    option_groups: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, int], list[str], list[str]]:
    """纯函数推导。option_groups: [{"name","code","options":[{"name","code","is_default"}]}]。

    返回 (dimensions, tags, sources)；sources 记录命中来源便于解释/排查。
    """
    dims: dict[str, int] = {}
    sources: list[str] = []
    name = _norm(product_name)
    desc = _norm(description)
    cat = _norm(category_name)
    name_blob = f"{name} {desc} {cat}".strip()

    # 1) 规格组默认/中位档
    for g in option_groups or []:
        dim = _group_dimension(_norm(g.get("name")), _norm(g.get("code")))
        if not dim:
            continue
        opts = list(g.get("options") or [])
        if not opts:
            continue
        chosen = None
        defaults = [o for o in opts if o.get("is_default")]
        if defaults:
            chosen = defaults[0]
        else:
            # 中位档：按选项排序取中间（保守代表典型口味）
            chosen = opts[len(opts) // 2]
        text = f"{chosen.get('code', '')} {chosen.get('name', '')}"
        lvl = _hit_level(text, _DIMENSION_LEVEL_RULES[dim])
        if lvl is not None and dim not in dims:
            dims[dim] = lvl
            sources.append(f"option:{dim}")

    # 2) 名称/描述/分类直接定级与标签（名称信号强于规格组默认档）
    for dim, rules in _DIMENSION_LEVEL_RULES.items():
        # 仅对辣/甜/酸/麻做名称定级；温度由规格组/冷热词决定更可靠
        if dim == "temperature":
            continue
        lvl = _hit_level(name_blob, rules)
        if lvl is not None:
            dims[dim] = lvl
            sources.append(f"name:{dim}")

    # 温度：名称里出现冰/热等强信号再定级
    lvl = _hit_level(name_blob, _DIMENSION_LEVEL_RULES["temperature"])
    if lvl is not None:
        dims["temperature"] = lvl
        sources.append("name:temperature")

    # 3) 受控标签词表（只从词表产出，杜绝脏标签）
    tags: list[str] = []
    for w in (*TEXTURE_VOCAB, *FLAVOR_VOCAB):
        if w in name_blob and w not in tags:
            tags.append(w)
            sources.append(f"tag:{w}")

    # 裁剪
    dims = {k: max(DIMENSION_META[k][1], min(DIMENSION_META[k][2], int(v))) for k, v in dims.items() if k in DIMENSION_META}
    tags = tags[:20]
    return dims, tags, sources


def _empty_overrides() -> dict[str, list[str]]:
    return {"dim": [], "tags_add": [], "tags_remove": []}


def _normalize_overrides(raw: Any) -> dict[str, list[str]]:
    base = _empty_overrides()
    if isinstance(raw, dict):
        for key in base:
            val = raw.get(key)
            if isinstance(val, list):
                base[key] = [_norm(v)[:_TAG_MAX_LEN] for v in val if _norm(v)]
    return base


def merge_manual(
    auto_dims: dict[str, int],
    auto_tags: list[str],
    overrides: dict[str, list[str]],
    current: ProductTasteProfile | None = None,
    manual_dims: dict[str, int] | None = None,
) -> tuple[dict[str, int], list[str]]:
    """合并 AUTO 推导与 MANUAL 接管。manual_dims 随 PUT 提供的手动维度值。"""
    ov = _normalize_overrides(overrides)
    dims = dict(auto_dims)
    for key in ov["dim"]:
        if key in DIMENSION_META:
            if manual_dims and key in manual_dims:
                lo, hi = DIMENSION_META[key][1], DIMENSION_META[key][2]
                dims[key] = max(lo, min(hi, int(manual_dims[key])))
            elif current and current.dimensions and key in (current.dimensions or {}):
                dims[key] = int(current.dimensions[key])
    remove = set(ov["tags_remove"])
    add = [t for t in ov["tags_add"] if t and t not in remove][:20]
    tags: list[str] = []
    for t in (*auto_tags, *add):
        if t in remove:
            continue
        if t not in tags:
            tags.append(t)
    return dims, tags[:20]


async def _load_product_context(db, product: Product):
    cat = await db.get(ProductCategory, product.category_id) if product.category_id else None
    pog = (
        await db.execute(
            select(ProductOptionGroup, OptionGroup)
            .join(OptionGroup, OptionGroup.id == ProductOptionGroup.group_id)
            .where(ProductOptionGroup.product_id == product.id, OptionGroup.status == 1)
            .order_by(ProductOptionGroup.sort_order)
        )
    ).all()
    gids = [g.id for _, g in pog]
    opts_by_group: dict[int, list[Option]] = {gid: [] for gid in gids}
    if gids:
        opts = (
            await db.execute(
                select(Option)
                .where(Option.group_id.in_(gids), Option.status == 1)
                .order_by(Option.sort_order, Option.id)
            )
        ).scalars().all()
        for o in opts:
            opts_by_group.setdefault(int(o.group_id), []).append(o)
    groups: list[dict[str, Any]] = []
    for _, g in pog:
        groups.append({
            "name": g.name,
            "code": g.code,
            "options": [
                {"name": o.name, "code": o.code, "is_default": bool(o.is_default)}
                for o in opts_by_group.get(g.id, [])
            ],
        })
    return cat, groups


async def recompute_product(db, product_id: int) -> ProductTasteProfile | None:
    """AUTO 重算：保留 MANUAL 接管键。无产品返回 None。"""
    product = await db.get(Product, product_id)
    if not product:
        return None
    cat, groups = await _load_product_context(db, product)
    auto_dims, auto_tags, sources = derive_dimensions_and_tags(
        product_name=product.name,
        description=product.description,
        category_name=cat.name if cat else None,
        option_groups=groups,
    )
    profile = (
        await db.execute(
            select(ProductTasteProfile).where(ProductTasteProfile.product_id == product_id)
        )
    ).scalar_one_or_none()
    overrides = _normalize_overrides(profile.manual_overrides if profile else None)
    manual_dims = profile.dimensions if profile else None
    dims, tags = merge_manual(auto_dims, auto_tags, overrides, profile, manual_dims)

    source = "MANUAL" if (overrides["dim"] or overrides["tags_add"] or overrides["tags_remove"]) else "AUTO"
    if profile:
        profile.dimensions = dims
        profile.tags = tags
        profile.source = source
        profile.derived_from = ",".join(sources)[:200]
    else:
        profile = ProductTasteProfile(
            product_id=product_id,
            store_id=int(product.store_id),
            dimensions=dims,
            tags=tags,
            source=source,
            manual_overrides=_empty_overrides(),
            derived_from=",".join(sources)[:200],
        )
        db.add(profile)
    await db.flush()
    return profile


async def upsert_manual(
    db,
    product_id: int,
    *,
    dimensions: dict[str, int] | None = None,
    tags_add: list[str] | None = None,
    tags_remove: list[str] | None = None,
    reset_dimensions: list[str] | None = None,
    reset_tags: list[str] | None = None,
) -> ProductTasteProfile | None:
    """店长微调：登记接管键后重算。reset_* 的键交回 AUTO。"""
    product = await db.get(Product, product_id)
    if not product:
        return None
    profile = (
        await db.execute(
            select(ProductTasteProfile).where(ProductTasteProfile.product_id == product_id)
        )
    ).scalar_one_or_none()
    overrides = _normalize_overrides(profile.manual_overrides if profile else None)
    if not profile:
        profile = ProductTasteProfile(
            product_id=product_id,
            store_id=int(product.store_id),
            dimensions={},
            tags=[],
            source="AUTO",
            manual_overrides=_empty_overrides(),
        )
        db.add(profile)
        await db.flush()

    # 先恢复自动（从接管集合移除）
    for k in reset_dimensions or []:
        if k in overrides["dim"]:
            overrides["dim"].remove(k)
    for t in reset_tags or []:
        if t in overrides["tags_add"]:
            overrides["tags_add"].remove(t)
        if t in overrides["tags_remove"]:
            overrides["tags_remove"].remove(t)

    # 再登记手动
    manual_dims: dict[str, int] = {}
    if dimensions:
        for k, v in dimensions.items():
            if k in DIMENSION_META:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                lo, hi = DIMENSION_META[k][1], DIMENSION_META[k][2]
                manual_dims[k] = max(lo, min(hi, iv))
                if k not in overrides["dim"]:
                    overrides["dim"].append(k)
    for t in (tags_add or [])[:_MANUAL_TAG_MAX_LEN]:
        t = _norm(t)[:_TAG_MAX_LEN]
        if t and t not in overrides["tags_add"]:
            overrides["tags_add"].append(t)
        if t in overrides["tags_remove"]:
            overrides["tags_remove"].remove(t)
    for t in (tags_remove or [])[:_MANUAL_TAG_MAX_LEN]:
        t = _norm(t)[:_TAG_MAX_LEN]
        if t and t not in overrides["tags_remove"]:
            overrides["tags_remove"].append(t)
        if t in overrides["tags_add"]:
            overrides["tags_add"].remove(t)

    profile.manual_overrides = overrides
    # 以 AUTO 基线 + overrides 合成（manual_dims 提供新值）
    cat, groups = await _load_product_context(db, product)
    auto_dims, auto_tags, _ = derive_dimensions_and_tags(
        product_name=product.name,
        description=product.description,
        category_name=cat.name if cat else None,
        option_groups=groups,
    )
    # 当前 profile.dimensions 作为未在本次提交的手动维度的现值
    merged_dims, merged_tags = merge_manual(auto_dims, auto_tags, overrides, profile, manual_dims)
    profile.dimensions = merged_dims
    profile.tags = merged_tags
    profile.source = "MANUAL" if any(overrides[k] for k in ("dim", "tags_add", "tags_remove")) else "AUTO"
    await db.flush()
    return profile


async def recompute_store(db, store_id: int) -> int:
    """整店重算（分类/规格批量变更后使用）。返回处理餐品数。"""
    pids = list(
        (
            await db.execute(select(Product.id).where(Product.store_id == store_id))
        ).scalars().all()
    )
    for pid in pids:
        try:
            await recompute_product(db, int(pid))
        except Exception:
            logger.exception("口味画像重算失败 product_id=%s", pid)
    return len(pids)


def profile_payload(profile: ProductTasteProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "dimensions": profile.dimensions or {},
        "tags": profile.tags or [],
        "source": profile.source,
        "manual_overrides": profile.manual_overrides or _empty_overrides(),
        "dimension_meta": {k: {"label": v[0], "min": v[1], "max": v[2]} for k, v in DIMENSION_META.items()},
        "tag_vocab": {"texture": list(TEXTURE_VOCAB), "flavor": list(FLAVOR_VOCAB)},
        "derived_from": profile.derived_from,
    }


async def get_or_derive(db, product_id: int) -> ProductTasteProfile | None:
    """读画像；不存在则即时推导（推荐冷数据兜底）。"""
    profile = (
        await db.execute(
            select(ProductTasteProfile).where(ProductTasteProfile.product_id == product_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = await recompute_product(db, product_id)
    return profile


async def maybe_settings_overrides(db) -> None:
    """预留：system_settings JSON 词表扩展。当前版本仅记录配置存在性，不做热替换。

    后续运营需要自定义词时，在 system_settings 写：
      taste_dimension_rules = {"spicy": [[2, ["MI_LA"]]]}, taste_tag_vocab = {"flavor": ["卤香"]}
    规则结构与常量一致，做表级合并即可。
    """
    return None
