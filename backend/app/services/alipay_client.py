"""支付宝开放平台客户端（预下单 / 退款，RSA2）"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.utils.alipay_keys import alipay_gateway_request, sign_alipay_params_rsa2

logger = logging.getLogger(__name__)


def _configured() -> bool:
    s = get_settings()
    return bool(s.alipay_app_id and s.alipay_private_key and s.alipay_public_key)


async def _gateway_call(method: str, biz_content: dict[str, Any], *, allow_codes: set[str] | None = None) -> dict[str, Any]:
    if not _configured():
        raise AppException(50020, "支付宝未完整配置（app_id/private_key/public_key）")
    settings = get_settings()
    params = {
        "app_id": settings.alipay_app_id,
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": settings.alipay_notify_url,
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    params["sign"] = sign_alipay_params_rsa2(params, settings.alipay_private_key)
    data = await alipay_gateway_request(params, timeout=20.0)
    resp_key = method.replace(".", "_") + "_response"
    body = data.get(resp_key) or data.get("error_response") or data
    ok_codes = allow_codes or {"10000"}
    if str(body.get("code")) not in ok_codes:
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


async def trade_bar_pay(
    *,
    payment_no: str,
    amount_yuan: Decimal,
    subject: str,
    auth_code: str,
) -> dict[str, Any]:
    """付款码支付（扫顾客支付宝付款码）。10000=成功；10003=等待用户付款需轮询查单。"""
    body = await _gateway_call(
        "alipay.trade.pay",
        {
            "out_trade_no": payment_no,
            "scene": "bar_code",
            "auth_code": auth_code.strip(),
            "total_amount": f"{amount_yuan:.2f}",
            "subject": subject[:256],
            "product_code": "FACE_TO_FACE_PAYMENT",
        },
        allow_codes={"10000", "10003"},
    )
    code = str(body.get("code") or "")
    trade_status = (body.get("trade_status") or "").upper()
    if code == "10003" or trade_status == "WAIT_BUYER_PAY":
        return {
            "provider": "ALIPAY",
            "payment_no": payment_no,
            "trade_status": "WAIT_BUYER_PAY",
            "waiting_payment": True,
            "amount": f"{amount_yuan:.2f}",
            "raw": body,
        }
    paid = trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED")
    return {
        "provider": "ALIPAY",
        "payment_no": payment_no,
        "trade_no": body.get("trade_no"),
        "trade_status": trade_status or "TRADE_SUCCESS",
        "amount": f"{amount_yuan:.2f}",
        "immediate_paid": paid,
        "provider_trade_no": body.get("trade_no"),
        "raw": body,
    }


async def trade_query(*, payment_no: str) -> dict[str, Any]:
    body = await _gateway_call("alipay.trade.query", {"out_trade_no": payment_no})
    return {
        "trade_no": body.get("trade_no"),
        "trade_status": body.get("trade_status"),
        "raw": body,
    }


async def trade_cancel(*, payment_no: str) -> dict[str, Any]:
    """撤销未支付订单（当面付建议超时后撤销，避免误付）。"""
    body = await _gateway_call("alipay.trade.cancel", {"out_trade_no": payment_no})
    return body


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
    # code=10000 表示接口调用成功；fund_change=Y 表示资金变动已发生（更接近退款成功）
    fund_change = str(body.get("fund_change") or "").upper()
    if fund_change == "Y":
        status = "SUCCESS"
    else:
        status = "PROCESSING"
    return {
        "refund_no": refund_no,
        "status": status,
        "provider_refund_no": body.get("trade_no"),
        "amount": f"{refund_yuan:.2f}",
        "raw": body,
    }


async def trade_refund_query(*, payment_no: str, refund_no: str) -> dict[str, Any]:
    """查询退款：refund_status=REFUND_SUCCESS 才表示退款成功。"""
    body = await _gateway_call(
        "alipay.trade.fastpay.refund.query",
        {
            "out_trade_no": payment_no,
            "out_request_no": refund_no,
        },
    )
    refund_status = str(body.get("refund_status") or "").upper()
    if refund_status == "REFUND_SUCCESS":
        status = "SUCCESS"
    elif refund_status:
        status = "FAILED"
    else:
        # 未返回 refund_status：可能尚未处理完
        status = "PROCESSING"
    return {
        "refund_no": body.get("out_request_no") or refund_no,
        "status": status,
        "provider_refund_no": body.get("trade_no"),
        "amount": body.get("refund_amount"),
        "raw": body,
    }
