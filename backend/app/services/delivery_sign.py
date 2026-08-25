"""美团 / 饿了么官方签名算法

美团外卖（waimaiopen）文档：
  参数按字典序 key=value&... 拼接，再 MD5(url + '?' + 参数串 + app_secret) 小写

饿了么商家开放平台（open-api.shop.ele.me）文档：
  signature = MD5(action + token + 排序后的 metas/params 键值对 + secret).upper()

饿了么旧版 openapi v2（兼容）：
  SHA1(hex(utf8(url + '?' + urlencode参数 + consumer_secret)))
"""
from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote_plus, unquote


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def meituan_sign(url: str, params: dict[str, Any], app_secret: str) -> str:
    """
    美团外卖开放平台签名。
    文档: https://developer.waimai.meituan.com / 签名 FAQ
    规则: MD5( 请求url + '?' + 排序参数(sig除外) + app_secret )，小写 32 位。
    计算签名时中文不编码；发送 HTTP 时再 UTF-8 编码。
    """
    items = []
    for k in sorted(params.keys()):
        if k == "sig":
            continue
        v = params[k]
        if v is None:
            continue
        items.append(f"{k}={v}")
    base = f"{url}?{'&'.join(items)}{app_secret}"
    return md5_hex(base)


def meituan_verify_callback(
    notify_url: str,
    params: dict[str, Any],
    app_secret: str,
    *,
    double_decode: bool = True,
) -> bool:
    """
    验签美团推送到商户的回调。
    POST 推送官方要求对参数解码 2 次后再算签；GET 解码 1 次。
    """
    sig = str(params.get("sig") or "")
    if not sig:
        return False
    cleaned: dict[str, Any] = {}
    for k, v in params.items():
        if k == "sig":
            continue
        s = "" if v is None else str(v)
        if double_decode:
            s = unquote(unquote(s))
        else:
            s = unquote(s)
        cleaned[k] = s
    expected = meituan_sign(notify_url, cleaned, app_secret)
    return expected == sig.lower()


def eleme_shop_sign(
    action: str,
    token: str,
    metas: dict[str, Any],
    params: dict[str, Any],
    secret: str,
) -> str:
    """
    饿了么商家开放平台（JSON-RPC）签名。
    signature = MD5(action + token + sorted(key=jsonvalue...) + secret).upper()
    参考官方 SDK / 开源实现。
    """
    merged: dict[str, Any] = {}
    merged.update(metas or {})
    merged.update(params or {})
    parts = []
    for k in sorted(merged.keys()):
        v = merged[k]
        if isinstance(v, (dict, list)):
            val = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        else:
            val = str(v)
        parts.append(f"{k}={val}")
    raw = f"{action}{token}{''.join(parts)}{secret}"
    return md5_hex(raw).upper()


def eleme_v2_sign(path_url: str, params: dict[str, Any], consumer_secret: str) -> str:
    """
    饿了么旧版 openapi v2 签名（官方示例代码）。
    https://openapi-doc.faas.ele.me/v2/appendix/sig-example-codes.html
    SHA1( hex(utf8(url + '?' + urlencode参数 + secret)) )
    """
    pairs = []
    for key in sorted(params.keys()):
        if key == "sig":
            continue
        val = params[key]
        if val is None:
            continue
        k = quote_plus(str(key), safe="")
        v = quote_plus(str(val), safe="")
        pairs.append(f"{k}={v}")
    string_a = "&".join(pairs)
    string_b = f"{path_url}?{string_a}{consumer_secret}"
    string_c = string_b.encode("utf-8").hex()
    return hashlib.sha1(string_c.encode("ascii")).hexdigest()


def flatten_form_body(body: bytes) -> dict[str, str]:
    """解析 application/x-www-form-urlencoded 或 JSON"""
    text = body.decode("utf-8")
    if text.strip().startswith("{"):
        data = json.loads(text)
        return {str(k): "" if v is None else str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False) for k, v in data.items()}
    from urllib.parse import parse_qsl

    return {k: v for k, v in parse_qsl(text, keep_blank_values=True)}
