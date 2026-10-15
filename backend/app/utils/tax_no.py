"""中国纳税人识别号 / 统一社会信用代码校验（GB 32100-2015）。"""
from __future__ import annotations

import re
import unicodedata

# 国标字符集：不含 I、O、Z、S、V
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_VAL = {c: i for i, c in enumerate(_USCC_CHARS)}
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
# 历史税号：15～20 位字母数字（18 位走国标校验）
_LEGACY_TAX_RE = re.compile(r"^[0-9A-Z]{15,20}$")
_NON_TAX_CHAR_RE = re.compile(r"[^0-9A-Z]+")


def _to_halfwidth(s: str) -> str:
    """全角字母数字转半角，便于粘贴营业执照上的税号。"""
    out: list[str] = []
    for ch in s:
        # NFKC 可将全角 ０-９／Ａ-Ｚ 等规范化为半角
        out.append(unicodedata.normalize("NFKC", ch))
    return "".join(out)


def normalize_tax_no(raw: str | None) -> str:
    """去空白、转大写、全角转半角，只保留字母数字。"""
    s = _to_halfwidth((raw or "").strip()).upper()
    # 去掉空格、横线、不可见字符等分隔符
    return _NON_TAX_CHAR_RE.sub("", s)


def is_valid_uscc(code: str) -> bool:
    """18 位统一社会信用代码：字符集 + 第 18 位加权模 31 校验。"""
    if len(code) != 18:
        return False
    if any(c not in _USCC_VAL for c in code):
        return False
    total = sum(_USCC_VAL[code[i]] * _USCC_WEIGHTS[i] for i in range(17))
    check_val = 31 - (total % 31)
    if check_val == 31:
        check_val = 0
    return code[17] == _USCC_CHARS[check_val]


def is_valid_company_tax_no(raw: str | None) -> bool:
    """
    单位税号：优先按 GB 32100 校验 18 位信用代码；
    另兼容历史 15～20 位税号（仅长度+字符集）。
    """
    return tax_no_error_message(raw) is None


def tax_no_error_message(raw: str | None) -> str | None:
    """合法返回 None；非法返回可读错误文案。"""
    code = normalize_tax_no(raw)
    if not code:
        return "单位抬头必须填写税号"
    if len(code) == 18:
        if any(c not in _USCC_VAL for c in code):
            return "税号含非法字符（不能含 I/O/Z/S/V）"
        if not is_valid_uscc(code):
            return "税号校验失败，请核对统一社会信用代码"
        return None
    if 15 <= len(code) <= 20 and _LEGACY_TAX_RE.match(code):
        return None
    return f"税号格式不正确（当前 {len(code)} 位，一般为 18 位统一社会信用代码）"
