"""门店前缀业务编码：{门店前缀}-{逻辑码}[ -子逻辑…]

规则：
- 总部共享（规格组 store_id=NULL）前缀 0000
- 门店数据前缀 = stores.store_no（如 SH01）
- 分类/餐品/规格组：{前缀}-{逻辑}，如 SH01-NiuRouMian
- 规格选项：{前缀}-{组逻辑}-{选项逻辑}，如 SH01-Liang-XiaoWan
- 逻辑段由汉字转拼音（每音节首字母大写）或用户输入的英文数字组成
"""
from __future__ import annotations

import re

HQ_STORE_PREFIX = "0000"

# 字段长度（须与 DDL 一致；store_no≤32 + '-' + 拼音逻辑）
MAX_CATEGORY_NO = 64
MAX_PRODUCT_NO = 64
MAX_GROUP_CODE = 64
MAX_OPTION_CODE = 80

_CODE_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$")


def normalize_store_prefix(store_no: str | None) -> str:
    raw = (store_no or "").strip()
    if not raw:
        return HQ_STORE_PREFIX
    return raw.upper()


def split_store_prefix(code: str) -> tuple[str | None, str]:
    raw = (code or "").strip()
    if not raw or "-" not in raw:
        return None, raw
    prefix, rest = raw.split("-", 1)
    rest = rest.strip()
    if not rest:
        return None, raw
    p = prefix.strip()
    if p.upper() == HQ_STORE_PREFIX or (p.isalnum() and 1 <= len(p) <= 32):
        return (HQ_STORE_PREFIX if p.upper() == HQ_STORE_PREFIX else p), rest
    return None, raw


def group_logical_key(code: str) -> str:
    _, rest = split_store_prefix(code)
    return rest.strip()


def option_logical_key(code: str, group_code: str | None = None) -> str:
    _, rest = split_store_prefix(code)
    rest = rest.strip()
    if not group_code:
        return rest
    g_key = group_logical_key(group_code)
    if g_key and rest.upper().startswith(g_key.upper() + "-"):
        return rest
    if g_key and "-" not in rest:
        return f"{g_key}-{rest}"
    return rest


def name_to_logical_code(name: str, *, max_len: int = 48) -> str:
    """汉字 → 拼音音节首字母大写拼接；英数保留。如 牛肉面 → NiuRouMian。"""
    from pypinyin import Style, lazy_pinyin

    raw = (name or "").strip()
    if not raw:
        return "Item"
    parts = lazy_pinyin(raw, style=Style.NORMAL, errors=lambda x: list(x))
    out: list[str] = []
    for p in parts:
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", str(p))
        if not cleaned:
            continue
        if cleaned.isdigit():
            out.append(cleaned)
        else:
            out.append(cleaned[:1].upper() + cleaned[1:].lower())
    stem = "".join(out) or "Item"
    if len(stem) > max_len:
        stem = stem[:max_len]
    return stem


def format_group_code(store_prefix: str, logical_or_raw: str) -> str:
    prefix = normalize_store_prefix(store_prefix)
    key = group_logical_key(logical_or_raw)
    if not key:
        raise ValueError("编码逻辑段不能为空")
    code = f"{prefix}-{key}"
    if len(code) > MAX_GROUP_CODE:
        raise ValueError(f"规格组编码过长（最多 {MAX_GROUP_CODE} 字符）")
    return code


def format_option_code(store_prefix: str, group_code: str, logical_or_raw: str) -> str:
    prefix = normalize_store_prefix(store_prefix)
    g_key = group_logical_key(group_code)
    opt_key = option_logical_key(logical_or_raw, group_code=group_code)
    if not opt_key:
        raise ValueError("选项编码不能为空")
    if g_key and not opt_key.upper().startswith(g_key.upper() + "-") and opt_key.upper() != g_key.upper():
        if "-" not in opt_key:
            opt_key = f"{g_key}-{opt_key}"
    code = f"{prefix}-{opt_key}"
    if len(code) > MAX_OPTION_CODE:
        raise ValueError(f"选项编码过长（最多 {MAX_OPTION_CODE} 字符）")
    return code


def format_entity_code(store_prefix: str, logical_or_raw: str, *, max_len: int) -> str:
    """分类编号 / 餐品编号（与规格组同形态）。"""
    prefix = normalize_store_prefix(store_prefix)
    key = group_logical_key(logical_or_raw)
    if not key:
        raise ValueError("编码逻辑段不能为空")
    code = f"{prefix}-{key}"
    if len(code) > max_len:
        raise ValueError(f"编码过长（最多 {max_len} 字符）: {code}")
    return code


def format_category_no(store_prefix: str, logical_or_raw: str) -> str:
    return format_entity_code(store_prefix, logical_or_raw, max_len=MAX_CATEGORY_NO)


def product_logical_key(code: str, category_logical: str | None = None) -> str:
    """从餐品编号取出餐品逻辑段（去掉门店前缀与分类段）。"""
    _, rest = split_store_prefix(code)
    rest = (rest or "").strip()
    if not rest:
        return ""
    if not category_logical:
        # 无分类时：若已是 分类-餐品 两段，整段保留供 format 再拼
        return rest
    cat = group_logical_key(category_logical)
    if not cat:
        return rest
    upper = rest.upper()
    prefix_cat = cat.upper() + "-"
    if upper.startswith(prefix_cat):
        return rest[len(cat) + 1 :]
    # 用户只填了餐品逻辑，或旧版两段编号
    if "-" not in rest:
        return rest
    # 旧编号仅「前缀-餐品」且餐品段不含分类：整段当餐品逻辑会变成 前缀-分类-旧整段，不理想
    # 若 rest 第一段 == cat，去掉；否则把 rest 最后一段当餐品名
    parts = rest.split("-")
    if parts[0].upper() == cat.upper() and len(parts) >= 2:
        return "-".join(parts[1:])
    return parts[-1]


def format_product_no(
    store_prefix: str,
    product_logical_or_raw: str,
    category_logical: str | None = None,
) -> str:
    """餐品编号：{前缀}-{分类逻辑}-{餐品逻辑}，如 0000-Mian-NiuRouMian / SH01-Mian-NiuRouMian。"""
    prefix = normalize_store_prefix(store_prefix)
    if category_logical:
        cat_raw = group_logical_key(category_logical) or (category_logical or "").strip()
        if cat_raw and re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", cat_raw):
            cat = cat_raw
        else:
            cat = name_to_logical_code(category_logical)
        if not cat:
            raise ValueError("分类逻辑段不能为空")
        prod = product_logical_key(product_logical_or_raw, category_logical=cat)
        if not prod:
            raw = group_logical_key(product_logical_or_raw)
            prod = product_logical_key(raw, category_logical=cat) if raw else ""
        if prod and not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", prod):
            prod = name_to_logical_code(prod)
        if not prod:
            raise ValueError("餐品编码逻辑段不能为空")
        code = f"{prefix}-{cat}-{prod}"
    else:
        key = group_logical_key(product_logical_or_raw)
        if not key:
            raise ValueError("餐品编码逻辑段不能为空")
        code = f"{prefix}-{key}"
    if len(code) > MAX_PRODUCT_NO:
        raise ValueError(f"餐品编号过长（最多 {MAX_PRODUCT_NO} 字符）: {code}")
    return code


def validate_prefixed_code(code: str, *, max_len: int, expect_prefix: str | None = None) -> str:
    """校验已规范化编码；返回 strip 后的 code。"""
    raw = (code or "").strip()
    if not raw:
        raise ValueError("编码不能为空")
    if len(raw) > max_len:
        raise ValueError(f"编码过长（最多 {max_len} 字符）")
    if not _CODE_RE.match(raw):
        raise ValueError("编码须为「前缀-逻辑段」，仅字母数字与连字符，如 0000-Mian-NiuRouMian")
    if expect_prefix is not None:
        prefix, _ = split_store_prefix(raw)
        want = normalize_store_prefix(expect_prefix)
        if prefix != want:
            raise ValueError(f"编码前缀应为 {want}，当前为 {prefix or '无'}")
    return raw


def suggest_code(
    *,
    name: str,
    store_prefix: str,
    kind: str = "product",
    group_code: str | None = None,
    category_logical: str | None = None,
) -> dict:
    """根据名称生成建议编码。"""
    prefix = normalize_store_prefix(store_prefix)
    logical = name_to_logical_code(name)
    if kind == "option":
        gc = group_code or f"{prefix}-Group"
        code = format_option_code(prefix, gc, logical)
        max_len = MAX_OPTION_CODE
    elif kind == "category":
        code = format_category_no(prefix, logical)
        max_len = MAX_CATEGORY_NO
    elif kind == "group":
        code = format_group_code(prefix, logical)
        max_len = MAX_GROUP_CODE
    else:
        code = format_product_no(prefix, logical, category_logical=category_logical)
        max_len = MAX_PRODUCT_NO
    return {
        "prefix": prefix,
        "logical": logical,
        "category_logical": group_logical_key(category_logical) if category_logical else None,
        "code": code,
        "max_len": max_len,
        "valid": True,
    }


def remap_code_to_prefix(src_code: str, target_prefix: str, *, group_code: str | None = None) -> str:
    prefix = normalize_store_prefix(target_prefix)
    if group_code is None:
        return format_group_code(prefix, src_code)
    return format_option_code(prefix, group_code, src_code)
