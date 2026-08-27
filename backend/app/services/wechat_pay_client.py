"""微信支付 APIv3 客户端（Native / JSAPI / 退款）"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)
WECHAT_API = "https://api.mch.weixin.qq.com"


def _load_private_key():
    settings = get_settings()
    path = Path(settings.wechat_private_key_path)
    if not path.is_file():
        raise AppException(50010, f"微信商户私钥不存在: {path}")
    return serialization.load_pem_private_key(path.read_text(encoding="utf-8").encode(), password=None)


def _sign_message(message: str) -> str:
    key = _load_private_key()
    sig = key.sign(message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("utf-8")


def _authorization(method: str, url_path: str, body: str) -> str:
    settings = get_settings()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"
    signature = _sign_message(message)
    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{settings.wechat_mch_id}",'
        f'nonce_str="{nonce}",signature="{signature}",'
        f'timestamp="{timestamp}",serial_no="{settings.wechat_cert_serial_no}"'
    )


def _configured() -> bool:
    s = get_settings()
    return bool(s.wechat_mch_id and s.wechat_api_v3_key and s.wechat_cert_serial_no and s.wechat_app_id)


async def _request(method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
    if not _configured():
        raise AppException(50011, "微信支付未完整配置（mch_id/api_v3_key/cert_serial/app_id）")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if payload is not None else ""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": _authorization(method, path, body),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(method, f"{WECHAT_API}{path}", content=body or None, headers=headers)
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        logger.error("微信 API 错误 %s %s: %s", method, path, data)
        raise AppException(50012, f"微信支付失败: {data.get('message') or data.get('code') or resp.status_code}")
    return data


async def create_native_order(
    *,
    payment_no: str,
    description: str,
    amount_yuan: Decimal,
    notify_url: str,
) -> dict[str, Any]:
    settings = get_settings()
    total_fen = int((amount_yuan * 100).quantize(Decimal("1")))
    data = await _request(
        "POST",
        "/v3/pay/transactions/native",
        {
            "appid": settings.wechat_app_id,
            "mchid": settings.wechat_mch_id,
            "description": description[:127],
            "out_trade_no": payment_no,
            "notify_url": notify_url or settings.wechat_notify_url,
            "amount": {"total": total_fen, "currency": "CNY"},
        },
    )
    return {"code_url": data.get("code_url"), "provider": "WECHAT", "payment_no": payment_no}


async def create_jsapi_order(
    *,
    payment_no: str,
    description: str,
    amount_yuan: Decimal,
    openid: str,
    notify_url: str,
) -> dict[str, Any]:
    settings = get_settings()
    total_fen = int((amount_yuan * 100).quantize(Decimal("1")))
    data = await _request(
        "POST",
        "/v3/pay/transactions/jsapi",
        {
            "appid": settings.wechat_app_id,
            "mchid": settings.wechat_mch_id,
            "description": description[:127],
            "out_trade_no": payment_no,
            "notify_url": notify_url or settings.wechat_notify_url,
            "amount": {"total": total_fen, "currency": "CNY"},
            "payer": {"openid": openid},
        },
    )
    prepay_id = data.get("prepay_id")
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    pkg = f"prepay_id={prepay_id}"
    pay_sign = _sign_message(f"{settings.wechat_app_id}\n{ts}\n{nonce}\n{pkg}\n")
    return {
        "provider": "WECHAT",
        "payment_no": payment_no,
        "prepay_id": prepay_id,
        "timeStamp": ts,
        "nonceStr": nonce,
        "package": pkg,
        "signType": "RSA",
        "paySign": pay_sign,
    }


async def refund(
    *,
    payment_no: str,
    refund_no: str,
    total_yuan: Decimal,
    refund_yuan: Decimal,
    reason: str,
) -> dict[str, Any]:
    settings = get_settings()
    notify = (settings.wechat_refund_notify_url or settings.wechat_notify_url or "").strip()
    payload: dict[str, Any] = {
        "out_trade_no": payment_no,
        "out_refund_no": refund_no,
        "reason": (reason or "退款")[:80],
        "amount": {
            "refund": int((refund_yuan * 100).quantize(Decimal("1"))),
            "total": int((total_yuan * 100).quantize(Decimal("1"))),
            "currency": "CNY",
        },
    }
    if notify:
        payload["notify_url"] = notify
    data = await _request("POST", "/v3/refund/domestic/refunds", payload)
    # 申请成功仅表示受理；SUCCESS 才是终态，其余保持 PROCESSING 等回调/查询
    raw_status = (data.get("status") or "PROCESSING").upper()
    if raw_status == "SUCCESS":
        status = "SUCCESS"
    elif raw_status in ("CLOSED", "ABNORMAL"):
        status = raw_status
    else:
        status = "PROCESSING"
    return {
        "refund_no": refund_no,
        "status": status,
        "provider_refund_no": data.get("refund_id"),
        "amount": str(refund_yuan),
        "raw": data,
    }


async def query_refund(*, refund_no: str) -> dict[str, Any]:
    """查询单笔退款（商户退款单号）。"""
    data = await _request("GET", f"/v3/refund/domestic/refunds/out-refund-no/{refund_no}")
    raw_status = (data.get("status") or "").upper()
    if raw_status == "SUCCESS":
        status = "SUCCESS"
    elif raw_status in ("CLOSED", "ABNORMAL"):
        status = raw_status
    elif raw_status:
        status = "PROCESSING"
    else:
        status = "PROCESSING"
    return {
        "refund_no": data.get("out_refund_no") or refund_no,
        "status": status,
        "provider_refund_no": data.get("refund_id"),
        "amount": data.get("amount", {}),
        "raw": data,
    }
