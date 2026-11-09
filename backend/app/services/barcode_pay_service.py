"""收银台付款码：发起支付 + 轮询查单（微信 USERPAYING / 支付宝 10003）。"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from app.core.exceptions import AppException
from app.services import alipay_client, wechat_pay_v2_client

logger = logging.getLogger(__name__)

# 官方建议：USERPAYING 先等 5 秒再查，间隔 5~10 秒，总时长约 45 秒
_WECHAT_POLL_INTERVAL_SEC = 5
_WECHAT_POLL_MAX_SEC = 45
_ALIPAY_POLL_INTERVAL_SEC = 3
_ALIPAY_POLL_MAX_SEC = 60


async def _poll_wechat(payment_no: str) -> dict[str, Any]:
    await asyncio.sleep(5)
    elapsed = 5
    while elapsed <= _WECHAT_POLL_MAX_SEC:
        q = await wechat_pay_v2_client.order_query(payment_no=payment_no)
        state = (q.get("trade_state") or "").upper()
        if state == "SUCCESS":
            return q
        if state in ("PAYERROR", "CLOSED", "REVOKED"):
            raise AppException(50012, f"微信支付失败: {state}")
        if state not in ("USERPAYING", "NOTPAY", "ACCEPT", ""):
            logger.warning("微信查单未知状态 payment=%s state=%s", payment_no, state)
        await asyncio.sleep(_WECHAT_POLL_INTERVAL_SEC)
        elapsed += _WECHAT_POLL_INTERVAL_SEC
    raise AppException(50013, "微信支付超时，请顾客确认是否已付款；未付款请重新扫码")


def _wechat_amount_from_result(result: dict[str, Any]) -> str:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    total_fee = raw.get("total_fee")
    if total_fee is None:
        raise AppException(50041, "微信付款码结果缺少 total_fee，禁止确认收款")
    try:
        return str(Decimal(str(total_fee)) / Decimal("100"))
    except Exception as exc:
        raise AppException(50041, "微信付款码 total_fee 非法") from exc


async def settle_wechat_barcode(
    *,
    payment_no: str,
    description: str,
    amount_yuan: Decimal,
    auth_code: str,
) -> dict[str, Any]:
    """微信付款码：micropay → USERPAYING 时轮询查单。"""
    first = await wechat_pay_v2_client.micropay(
        payment_no=payment_no,
        description=description,
        amount_yuan=amount_yuan,
        auth_code=auth_code,
    )
    state = (first.get("trade_state") or "").upper()
    if state == "SUCCESS":
        return {
            "provider": "WECHAT",
            "payment_no": payment_no,
            "trade_state": "SUCCESS",
            "transaction_id": first.get("transaction_id"),
            "immediate_paid": True,
            "provider_trade_no": first.get("transaction_id"),
            "provider_amount": _wechat_amount_from_result(first),
        }
    if state in ("USERPAYING", "UNKNOWN"):
        q = await _poll_wechat(payment_no)
        return {
            "provider": "WECHAT",
            "payment_no": payment_no,
            "trade_state": "SUCCESS",
            "transaction_id": q.get("transaction_id"),
            "immediate_paid": True,
            "provider_trade_no": q.get("transaction_id"),
            "provider_amount": _wechat_amount_from_result(q),
        }
    raise AppException(50012, f"微信支付失败: {state or '未知'}")


async def _poll_alipay(payment_no: str) -> dict[str, Any]:
    elapsed = 0
    while elapsed <= _ALIPAY_POLL_MAX_SEC:
        q = await alipay_client.trade_query(payment_no=payment_no)
        status = (q.get("trade_status") or "").upper()
        if status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return q
        if status in ("TRADE_CLOSED",):
            raise AppException(50021, "支付宝交易已关闭")
        await asyncio.sleep(_ALIPAY_POLL_INTERVAL_SEC)
        elapsed += _ALIPAY_POLL_INTERVAL_SEC
    raise AppException(50022, "支付宝支付超时，请顾客确认是否已付款；未付款请重新扫码")


async def settle_alipay_barcode(
    *,
    payment_no: str,
    amount_yuan: Decimal,
    subject: str,
    auth_code: str,
) -> dict[str, Any]:
    """支付宝付款码：trade.pay → 10003 等待付款时轮询 trade.query。"""
    first = await alipay_client.trade_bar_pay(
        payment_no=payment_no,
        amount_yuan=amount_yuan,
        subject=subject,
        auth_code=auth_code,
    )
    if first.get("immediate_paid"):
        return first
    if first.get("waiting_payment"):
        q = await _poll_alipay(payment_no)
        return {
            "provider": "ALIPAY",
            "payment_no": payment_no,
            "trade_status": q.get("trade_status") or "TRADE_SUCCESS",
            "trade_no": q.get("trade_no"),
            "immediate_paid": True,
            "provider_trade_no": q.get("trade_no"),
            "provider_amount": q.get("total_amount"),
        }
    raise AppException(50021, "支付宝支付未完成")
