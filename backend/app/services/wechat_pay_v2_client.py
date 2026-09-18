"""微信支付 V2：付款码 micropay / 查单 orderquery（官方要求付款码仍走 V2）。"""
from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)
WECHAT_API = "https://api.mch.weixin.qq.com"


def _api_key() -> str:
    # P1-12: 优先使用独立的 V2 API 密钥，未配置时回退 V3 密钥（兼容）
    s = get_settings()
    key = (s.wechat_api_v2_key or "").strip()
    if not key:
        key = (s.wechat_api_v3_key or "").strip()
    if not key:
        raise AppException(50011, "微信 API 密钥未配置（WECHAT_API_V2_KEY 或 WECHAT_API_V3_KEY）")
    return key


def _sign_md5(params: dict[str, str]) -> str:
    items = sorted((k, v) for k, v in params.items() if v and k != "sign")
    content = "&".join(f"{k}={v}" for k, v in items) + f"&key={_api_key()}"
    return hashlib.md5(content.encode("utf-8")).hexdigest().upper()


def _xml_dict(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    return {child.tag: (child.text or "") for child in root}


def _dict_xml(params: dict[str, str]) -> str:
    parts = ["<xml>"]
    for k, v in params.items():
        parts.append(f"<{k}><![CDATA[{v}]]></{k}>")
    parts.append("</xml>")
    return "".join(parts)


def _configured() -> bool:
    s = get_settings()
    return bool(s.wechat_mch_id and s.wechat_app_id and s.wechat_api_v3_key)


async def _post_xml(path: str, params: dict[str, str]) -> dict[str, str]:
    if not _configured():
        raise AppException(50011, "微信支付未完整配置（app_id/mch_id/api_key）")
    payload = dict(params)
    payload.setdefault("nonce_str", uuid.uuid4().hex[:32])
    payload["sign"] = _sign_md5(payload)
    body = _dict_xml(payload)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{WECHAT_API}{path}", content=body.encode("utf-8"))
    data = _xml_dict(resp.text)
    if data.get("return_code") != "SUCCESS":
        raise AppException(50012, f"微信支付通信失败: {data.get('return_msg') or data}")
    # P1-12: 响应签名校验 — 防止中间人篡改响应（fail-closed：result_code 存在但 sign 缺失也判失败）
    resp_sign = data.pop("sign", None)
    if data.get("result_code"):
        if not resp_sign:
            raise AppException(50012, "微信支付响应缺少签名，拒绝处理")
        expected_sign = _sign_md5(data)
        if resp_sign.upper() != expected_sign:
            raise AppException(50012, "微信支付响应签名校验失败")
    # result_code 可能为 FAIL（如 USERPAYING），由上层处理
    return data


async def micropay(
    *,
    payment_no: str,
    description: str,
    amount_yuan: Decimal,
    auth_code: str,
) -> dict[str, Any]:
    """提交付款码支付。可能返回 trade_state=SUCCESS 或 err_code=USERPAYING。"""
    settings = get_settings()
    total_fee = int((amount_yuan * 100).quantize(Decimal("1")))
    data = await _post_xml(
        "/pay/micropay",
        {
            "appid": settings.wechat_app_id,
            "mch_id": settings.wechat_mch_id,
            "body": description[:127],
            "out_trade_no": payment_no,
            "total_fee": str(total_fee),
            "spbill_create_ip": "127.0.0.1",
            "auth_code": auth_code.strip(),
        },
    )
    if data.get("return_code") == "SUCCESS" and data.get("result_code") == "SUCCESS":
        return {
            "trade_state": "SUCCESS",
            "transaction_id": data.get("transaction_id"),
            "raw": data,
        }
    err = (data.get("err_code") or "").upper()
    if err == "USERPAYING":
        return {"trade_state": "USERPAYING", "raw": data}
    if err == "SYSTEMERROR":
        return {"trade_state": "UNKNOWN", "raw": data}
    msg = data.get("err_code_des") or data.get("return_msg") or err or "支付失败"
    raise AppException(50012, f"微信支付失败: {msg}")


async def order_query(*, payment_no: str) -> dict[str, Any]:
    """查询订单。trade_state: SUCCESS / USERPAYING / NOTPAY / CLOSED / PAYERROR 等。"""
    settings = get_settings()
    data = await _post_xml(
        "/pay/orderquery",
        {
            "appid": settings.wechat_app_id,
            "mch_id": settings.wechat_mch_id,
            "out_trade_no": payment_no,
        },
    )
    if data.get("return_code") != "SUCCESS":
        raise AppException(50012, f"微信查单失败: {data.get('return_msg') or data}")
    if data.get("result_code") != "SUCCESS":
        err = data.get("err_code") or ""
        if err == "ORDERNOTEXIST":
            return {"trade_state": "NOTPAY", "raw": data}
        raise AppException(50012, f"微信查单失败: {data.get('err_code_des') or err}")
    return {
        "trade_state": (data.get("trade_state") or "").upper(),
        "transaction_id": data.get("transaction_id"),
        "raw": data,
    }
