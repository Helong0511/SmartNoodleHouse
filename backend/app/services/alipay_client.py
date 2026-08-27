"""支付宝开放平台客户端（预下单 / 退款，RSA2）"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


def _load_private_key(pem: str):
    pem = pem.strip()
    if not pem.startswith("-----"):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + "\n".join(pem[i : i + 64] for i in range(0, len(pem), 64))
            + "\n-----END RSA PRIVATE KEY-----"
        )
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _sign(params: dict[str, str], private_key_pem: str) -> str:
    items = sorted((k, v) for k, v in params.items() if v is not None and v != "" and k not in ("sign", "sign_type"))
    content = "&".join(f"{k}={v}" for k, v in items)
    key = _load_private_key(private_key_pem)
    sig = key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("utf-8")


def _configured() -> bool:
    s = get_settings()
    return bool(s.alipay_app_id and s.alipay_private_key and s.alipay_public_key)


async def _gateway_call(method: str, biz_content: dict[str, Any]) -> dict[str, Any]:
    if not _configured():
        raise AppException(50020, "支付宝未完整配置（app_id/private_key/public_key）")
    settings = get_settings()
    params = {
        "app_id": settings.alipay_app_id,
        "method": method,
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": settings.alipay_notify_url,
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    params["sign"] = _sign(params, settings.alipay_private_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(settings.alipay_gateway, data=params)
    data = resp.json()
    # 响应 key 形如 alipay_trade_precreate_response
    resp_key = method.replace(".", "_") + "_response"
    body = data.get(resp_key) or data
    if str(body.get("code")) not in ("10000",):
        logger.error("支付宝 API 错误 %s: %s", method, body)
        raise AppException(50021, f"支付宝失败: {body.get('sub_msg') or body.get('msg') or body}")
    return body


async def trade_precreate(*, payment_no: str, amount_yuan: Decimal, subject: str) -> dict[str, Any]:
    body = await _gateway_call(
        "alipay.trade.precreate",
        {
            "out_trade_no": payment_no,
            "total_amount": f"{amount_yuan:.2f}",
            "subject": subject[:256],
        },
    )
    return {
        "provider": "ALIPAY",
        "payment_no": payment_no,
        "qr_code": body.get("qr_code"),
        "amount": f"{amount_yuan:.2f}",
    }


async def trade_refund(
    *,
    payment_no: str,
    refund_no: str,
    refund_yuan: Decimal,
    reason: str,
) -> dict[str, Any]:
    body = await _gateway_call(
        "alipay.trade.refund",
        {
            "out_trade_no": payment_no,
            "out_request_no": refund_no,
            "refund_amount": f"{refund_yuan:.2f}",
            "refund_reason": (reason or "退款")[:256],
        },
    )
    return {
        "refund_no": refund_no,
        "status": "SUCCESS" if str(body.get("code")) == "10000" else "FAILED",
        "provider_refund_no": body.get("trade_no"),
        "amount": f"{refund_yuan:.2f}",
    }
