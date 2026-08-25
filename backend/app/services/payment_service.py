import json
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import Order, Payment
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier


class PaymentProvider(ABC):
    @abstractmethod
    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        ...

    @abstractmethod
    async def query_payment(self, payment: Payment) -> dict:
        ...

    @abstractmethod
    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str) -> dict:
        ...

    @abstractmethod
    async def verify_callback(self, headers: dict, body: bytes) -> bool:
        ...

    @abstractmethod
    async def handle_callback(self, data: dict) -> dict:
        ...


class WechatPayProvider(PaymentProvider):
    """微信支付 Provider - 需配置真实密钥后启用"""

    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        # 生产环境调用微信统一下单 API
        return {
            "provider": "WECHAT",
            "payment_no": payment.payment_no,
            "prepay_id": f"wx_prepay_{payment.payment_no}",
            "code_url": f"weixin://wxpay/bizpayurl?pr={payment.payment_no}",
            "amount": str(payment.amount),
        }

    async def query_payment(self, payment: Payment) -> dict:
        return {"status": payment.status, "provider_trade_no": payment.provider_trade_no}

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str) -> dict:
        return {"refund_no": generate_no("RF"), "status": "SUCCESS", "amount": str(amount)}

    async def verify_callback(self, headers: dict, body: bytes) -> bool:
        try:
            WechatPayVerifier().verify_and_parse(headers, body)
            return True
        except Exception as e:
            logger.error("微信回调验签失败: %s", e)
            return False

    async def handle_callback(self, data: dict) -> dict:
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("transaction_id"),
            "amount": data.get("amount", {}).get("total", 0),
            "status": "PAID" if data.get("trade_state") == "SUCCESS" else "FAILED",
        }


class AlipayProvider(PaymentProvider):
    """支付宝 Provider - 需配置真实密钥后启用"""

    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        return {
            "provider": "ALIPAY",
            "payment_no": payment.payment_no,
            "qr_code": f"https://qr.alipay.com/{payment.payment_no}",
            "amount": str(payment.amount),
        }

    async def query_payment(self, payment: Payment) -> dict:
        return {"status": payment.status, "provider_trade_no": payment.provider_trade_no}

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str) -> dict:
        return {"refund_no": generate_no("RF"), "status": "SUCCESS", "amount": str(amount)}

    async def verify_callback(self, headers: dict, body: bytes) -> bool:
        try:
            AlipayVerifier().verify_and_parse(headers, body)
            return True
        except Exception as e:
            logger.error("支付宝回调验签失败: %s", e)
            return False

    async def handle_callback(self, data: dict) -> dict:
        trade_status = data.get("trade_status", "")
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("trade_no"),
            "amount": data.get("total_amount", "0"),
            "status": "PAID" if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED") else "FAILED",
        }


class PaymentService:
    PROVIDERS = {
        "WECHAT": WechatPayProvider,
        "ALIPAY": AlipayProvider,
    }

    CHANNEL_MAP = {
        ("WECHAT", "h5"): "WECHAT_H5",
        ("WECHAT", "native"): "WECHAT_NATIVE",
        ("WECHAT", "miniprogram"): "WECHAT_MINIPROGRAM",
        ("ALIPAY", "qr"): "ALIPAY_QR",
        ("ALIPAY", "h5"): "ALIPAY_H5",
        ("ALIPAY", "miniprogram"): "ALIPAY_MINIAPP",
    }

    @classmethod
    def get_provider(cls, provider: str) -> PaymentProvider:
        p = cls.PROVIDERS.get(provider.upper())
        if not p:
            raise AppException(50001, f"不支持的支付渠道: {provider}")
        return p()

    @classmethod
    async def create_payment(
        cls,
        db: AsyncSession,
        order: Order,
        provider: str,
        channel_type: str = "native",
        customer_id: int | None = None,
    ) -> tuple[Payment, dict]:
        if order.payment_status == "PAID":
            raise AppException(40010, "订单已支付")
        if order.status == "CANCELLED":
            raise AppException(40011, "订单已取消")

        channel = cls.CHANNEL_MAP.get((provider.upper(), channel_type.lower()), f"{provider.upper()}_{channel_type.upper()}")
        payment = Payment(
            payment_no=generate_no("PAY"),
            order_id=order.id,
            customer_id=customer_id or order.customer_id,
            provider=provider.upper(),
            channel=channel,
            amount=order.total_amount,
            status="PENDING",
        )
        db.add(payment)
        await db.flush()

        provider_inst = cls.get_provider(provider)
        pay_data = await provider_inst.create_payment(payment, order)
        return payment, pay_data

    @classmethod
    async def handle_callback(
        cls,
        db: AsyncSession,
        provider: str,
        headers: dict,
        body: bytes,
    ) -> Payment | None:
        provider_inst = cls.get_provider(provider)

        if provider.upper() == "WECHAT":
            data = WechatPayVerifier().verify_and_parse(headers, body)
        else:
            data = AlipayVerifier().verify_and_parse(headers, body)

        parsed = await provider_inst.handle_callback(data)

        result = await db.execute(select(Payment).where(Payment.payment_no == parsed["payment_no"]))
        payment = result.scalar_one_or_none()
        if not payment:
            raise AppException(40401, "支付记录不存在")

        # 幂等: 已支付直接返回
        if payment.status == "PAID":
            return payment

        order = await db.get(Order, payment.order_id)
        if not order:
            raise AppException(40402, "订单不存在")

        raw_amount = parsed.get("amount", 0)
        if isinstance(raw_amount, dict):
            raw_amount = raw_amount.get("total", 0)
        # 微信回调单位为分，支付宝为元
        if provider.upper() == "WECHAT":
            callback_amount = money(Decimal(str(raw_amount)) / 100)
        else:
            callback_amount = money(Decimal(str(raw_amount)))
        if callback_amount != payment.amount:
            raise AppException(40302, "支付金额不匹配")

        if parsed["status"] == "PAID":
            payment.status = "PAID"
            payment.provider_trade_no = parsed.get("provider_trade_no")
            payment.paid_at = now_local()
            payment.callback_raw = body.decode()

            order.payment_status = "PAID"
            order.status = "PAID"
            order.paid_at = now_local()
            order.payment_method = provider.upper()

        await db.flush()
        return payment
