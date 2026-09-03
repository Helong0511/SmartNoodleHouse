"""支付宝 RSA 密钥加载：兼容密钥工具默认 PKCS8 与旧版 PKCS1。"""
from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives import serialization


def _normalize_key_blob(raw: str) -> str:
    s = (raw or "").strip().strip('"').strip("'")
    # .env 多行或复制时可能带 \\n
    s = s.replace("\\n", "\n").strip()
    return s


def _wrap_b64(b64: str, begin: str, end: str) -> str:
    body = "".join(b64.split())
    lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {begin}-----\n{lines}\n-----END {end}-----\n"


def load_alipay_private_key(raw: str):
    """加载应用私钥。支付宝密钥工具默认 PKCS8（BEGIN PRIVATE KEY）。"""
    key_str = _normalize_key_blob(raw)
    if not key_str:
        raise ValueError("ALIPAY_PRIVATE_KEY 为空")

    candidates: list[str] = []
    if "BEGIN" in key_str:
        candidates.append(key_str if key_str.endswith("\n") else key_str + "\n")
    else:
        # 先 PKCS8（工具默认），再 PKCS1
        candidates.append(_wrap_b64(key_str, "PRIVATE KEY", "PRIVATE KEY"))
        candidates.append(_wrap_b64(key_str, "RSA PRIVATE KEY", "RSA PRIVATE KEY"))

    last_err: Exception | None = None
    for pem in candidates:
        try:
            return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except Exception as exc:
            last_err = exc
    raise ValueError(
        "ALIPAY_PRIVATE_KEY 无法解析（请填密钥工具生成的「应用私钥」，"
        "可用 PKCS8 或 PKCS1；勿填应用公钥/支付宝公钥/AES）"
    ) from last_err


def load_alipay_public_key(raw: str):
    """加载支付宝公钥。开放平台复制的多为 SubjectPublicKeyInfo（BEGIN PUBLIC KEY）。"""
    key_str = _normalize_key_blob(raw)
    if not key_str:
        raise ValueError("ALIPAY_PUBLIC_KEY 为空")

    candidates: list[str] = []
    if "BEGIN" in key_str:
        candidates.append(key_str if key_str.endswith("\n") else key_str + "\n")
    else:
        candidates.append(_wrap_b64(key_str, "PUBLIC KEY", "PUBLIC KEY"))
        candidates.append(_wrap_b64(key_str, "RSA PUBLIC KEY", "RSA PUBLIC KEY"))

    last_err: Exception | None = None
    for pem in candidates:
        try:
            return serialization.load_pem_public_key(pem.encode("utf-8"))
        except Exception as exc:
            last_err = exc
    raise ValueError(
        "ALIPAY_PUBLIC_KEY 无法解析（请填开放平台显示的「支付宝公钥」，勿填应用公钥）"
    ) from last_err


def decode_alipay_http_body(content: bytes, content_type: str | None = None) -> str:
    """支付宝网关偶发返回 GBK，不能一律按 UTF-8 解码。"""
    raw = content or b""
    if not raw:
        return ""
    ct = (content_type or "").lower()
    ordered: list[str] = []
    if "gbk" in ct or "gb2312" in ct or "gb18030" in ct:
        ordered.extend(["gb18030", "gbk", "utf-8"])
    else:
        ordered.extend(["utf-8", "gb18030", "gbk"])
    for enc in ordered:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_alipay_http_json(content: bytes, content_type: str | None = None) -> dict[str, Any]:
    text = decode_alipay_http_body(content, content_type)
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("支付宝响应不是 JSON 对象")
    return data


def build_alipay_sign_content(params: dict[str, Any]) -> str:
    """待签名字符串：剔除 sign 与空值，按 key ASCII 排序（须含 sign_type）。"""
    items = sorted(
        (str(k), str(v))
        for k, v in params.items()
        if v is not None and str(v) != "" and str(k) != "sign"
    )
    return "&".join(f"{k}={v}" for k, v in items)


def sign_alipay_params_rsa2(params: dict[str, Any], private_key_pem: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    content = build_alipay_sign_content(params)
    private_key = load_alipay_private_key(private_key_pem)
    signature = private_key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")


def encode_alipay_query(params: dict[str, Any]) -> str:
    """
    请求查询串编码：与官方自签名约定一致，使用 quote（空格→%20，非 quote_plus）。
    charset 等参数必须出现在 URL 查询字符串中，否则网关报验签出错。
    """
    from urllib.parse import quote

    items = [(str(k), str(v)) for k, v in params.items() if v is not None and str(v) != ""]
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in items)


async def alipay_gateway_request(params: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
    """向 openapi.alipay.com 发 GET（参数全部在 URL 查询串）。"""
    import httpx

    from app.core.config import get_settings

    settings = get_settings()
    gateway = (settings.alipay_gateway or "https://openapi.alipay.com/gateway.do").strip().rstrip("?&")
    query = encode_alipay_query(params)
    url = f"{gateway}?{query}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
    return parse_alipay_http_json(resp.content, resp.headers.get("content-type"))
