"""支付服务：真实创建/退款 + 验签回调 + 金额/商户校验 + 防重放 + 状态机"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import Order, Payment, PaymentCallbackReceipt, PaymentRefund
from app.services import alipay_client, wechat_pay_client
from app.services.order_state_machine import assert_payment_transition
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.status_history import record_order_status, record_payment_status
from app.services.webhook_replay import claim_once, make_receipt_key

logger = logging.getLogger(__name__)


class WechatPayProvider:
    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        settings = get_settings()
        if not (settings.wechat_mch_id and settings.wechat_api_v3_key and settings.wechat_cert_serial_no):
            if settings.is_development and settings.allow_payment_mock:
                logger.warning("微信支付 MOCK 下单（仅开发）")
                return {
                    "provider": "WECHAT",
                    "payment_no": payment.payment_no,
                    "prepay_id": f"wx_mock_{payment.payment_no}",
                    "code_url": f"weixin://wxpay/bizpayurl?pr={payment.payment_no}",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            raise AppException(50011, "微信支付未配置，拒绝创建支付")

        channel = (payment.channel or "").upper()
        desc = f"订单{order.order_no}"
        if "MINIPROGRAM" in channel or "JSAPI" in channel:
            openid = (extra or {}).get("openid")
            if not openid:
                raise AppException(40040, "小程序支付缺少 openid")
            data = await wechat_pay_client.create_jsapi_order(
                payment_no=payment.payment_no,
                description=desc,
                amount_yuan=payment.amount,
                openid=openid,
                notify_url=settings.wechat_notify_url,
            )
        else:
            data = await wechat_pay_client.create_native_order(
                payment_no=payment.payment_no,
                description=desc,
                amount_yuan=payment.amount,
                notify_url=settings.wechat_notify_url,
            )
        data["amount"] = str(payment.amount)
        return data

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str, refund_no: str) -> dict:
        settings = get_settings()
        if not (settings.wechat_mch_id and settings.wechat_api_v3_key):
            if settings.is_development and settings.allow_payment_mock:
                return {"refund_no": refund_no, "status": "SUCCESS", "amount": str(amount), "_mock": True}
            raise AppException(50011, "微信支付未配置，拒绝退款")
        return await wechat_pay_client.refund(
            payment_no=payment.payment_no,
            refund_no=refund_no,
            total_yuan=payment.amount,
            refund_yuan=amount,
            reason=reason,
        )

    async def handle_callback(self, data: dict) -> dict:
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("transaction_id"),
            "amount": data.get("amount", {}).get("total", 0) if isinstance(data.get("amount"), dict) else data.get("amount", 0),
            "status": "PAID" if data.get("trade_state") == "SUCCESS" else "FAILED",
            "mchid": data.get("mchid"),
        }


class AlipayProvider:
    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        settings = get_settings()
        if not (settings.alipay_app_id and settings.alipay_private_key):
            if settings.is_development and settings.allow_payment_mock:
                logger.warning("支付宝 MOCK 下单（仅开发）")
                return {
                    "provider": "ALIPAY",
                    "payment_no": payment.payment_no,
                    "qr_code": f"https://qr.alipay.com/{payment.payment_no}",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            raise AppException(50020, "支付宝未配置，拒绝创建支付")
        return await alipay_client.trade_precreate(
            payment_no=payment.payment_no,
            amount_yuan=payment.amount,
            subject=f"订单{order.order_no}",
        )

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str, refund_no: str) -> dict:
        settings = get_settings()
        if not (settings.alipay_app_id and settings.alipay_private_key):
            if settings.is_development and settings.allow_payment_mock:
                return {"refund_no": refund_no, "status": "SUCCESS", "amount": str(amount), "_mock": True}
            raise AppException(50020, "支付宝未配置，拒绝退款")
        return await alipay_client.trade_refund(
            payment_no=payment.payment_no,
            refund_no=refund_no,
            refund_yuan=amount,
            reason=reason,
        )

    async def handle_callback(self, data: dict) -> dict:
        trade_status = data.get("trade_status", "")
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("trade_no"),
            "amount": data.get("total_amount", "0"),
            "status": "PAID" if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED") else "FAILED",
            "app_id": data.get("app_id"),
        }


class PaymentService:
    PROVIDERS = {"WECHAT": WechatPayProvider, "ALIPAY": AlipayProvider}
    CHANNEL_MAP = {
        ("WECHAT", "h5"): "WECHAT_H5",
        ("WECHAT", "native"): "WECHAT_NATIVE",
        ("WECHAT", "miniprogram"): "WECHAT_MINIPROGRAM",
        ("ALIPAY", "qr"): "ALIPAY_QR",
        ("ALIPAY", "h5"): "ALIPAY_H5",
        ("ALIPAY", "miniprogram"): "ALIPAY_MINIAPP",
    }

    @classmethod
    def get_provider(cls, provider: str):
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
        extra: dict | None = None,
    ) -> tuple[Payment, dict]:
        if order.payment_status == "PAID":
            raise AppException(40010, "订单已支付")
        if order.status == "CANCELLED":
            raise AppException(40011, "订单已取消")
        if order.status != "PENDING_PAYMENT":
            raise AppException(40012, f"当前订单状态不可支付: {order.status}")

        channel = cls.CHANNEL_MAP.get(
            (provider.upper(), channel_type.lower()),
            f"{provider.upper()}_{channel_type.upper()}",
        )
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
        await record_payment_status(db, payment_id=payment.id, from_status=None, to_status="PENDING", source="CREATE")

        provider_inst = cls.get_provider(provider)
        pay_data = await provider_inst.create_payment(payment, order, extra)
        return payment, pay_data

    @classmethod
    async def handle_callback(
        cls,
        db: AsyncSession,
        provider: str,
        headers: dict,
        body: bytes,
    ) -> Payment | None:
        provider_u = provider.upper()
        provider_inst = cls.get_provider(provider_u)

        if provider_u == "WECHAT":
            data = WechatPayVerifier().verify_and_parse(headers, body)
            receipt = make_receipt_key(
                provider_u,
                headers.get("wechatpay-nonce") or headers.get("Wechatpay-Nonce") or "",
                data.get("out_trade_no") or "",
                data.get("transaction_id") or "",
            )
        else:
            data = AlipayVerifier().verify_and_parse(headers, body)
            receipt = make_receipt_key(
                provider_u,
                str(data.get("notify_id") or ""),
                str(data.get("out_trade_no") or ""),
                str(data.get("trade_no") or ""),
            )

        first = await claim_once(f"pay:{provider_u}", receipt)
        if not first:
            # 重复回调：仍按幂等返回已有支付
            parsed_early = await provider_inst.handle_callback(data)
            result = await db.execute(select(Payment).where(Payment.payment_no == parsed_early.get("payment_no")))
            return result.scalar_one_or_none()

        # DB 双保险
        try:
            db.add(PaymentCallbackReceipt(provider=provider_u, receipt_key=receipt, payment_no=data.get("out_trade_no")))
            await db.flush()
        except Exception:
            await db.rollback()
            parsed_early = await provider_inst.handle_callback(data)
            result = await db.execute(select(Payment).where(Payment.payment_no == parsed_early.get("payment_no")))
            return result.scalar_one_or_none()

        parsed = await provider_inst.handle_callback(data)
        result = await db.execute(
            select(Payment).where(Payment.payment_no == parsed["payment_no"]).with_for_update()
        )
        payment = result.scalar_one_or_none()
        if not payment:
            raise AppException(40401, "支付记录不存在")

        if payment.status == "PAID":
            return payment

        order = (
            await db.execute(select(Order).where(Order.id == payment.order_id).with_for_update())
        ).scalar_one_or_none()
        if not order:
            raise AppException(40402, "订单不存在")
        # 超时关单与回调竞态：已取消则拒绝入账
        if order.status == "CANCELLED" or order.payment_status == "CANCELLED":
            raise AppException(40901, "订单已关闭，拒绝支付入账")

        # 商户校验
        settings = get_settings()
        if provider_u == "WECHAT":
            mch = parsed.get("mchid") or data.get("mchid")
            if mch and str(mch) != str(settings.wechat_mch_id):
                raise AppException(40301, "支付商户号不匹配")
        else:
            app_id = parsed.get("app_id") or data.get("app_id")
            if app_id and settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
                raise AppException(40301, "支付宝 app_id 不匹配")

        raw_amount = parsed.get("amount", 0)
        if isinstance(raw_amount, dict):
            raw_amount = raw_amount.get("total", 0)
        if provider_u == "WECHAT":
            callback_amount = money(Decimal(str(raw_amount)) / 100)
        else:
            callback_amount = money(Decimal(str(raw_amount)))
        if callback_amount != money(payment.amount):
            raise AppException(40302, "支付金额不匹配")

        if parsed["status"] == "PAID":
            assert_payment_transition(order.status, "PAID")
            old_p = payment.status
            old_o = order.status
            payment.status = "PAID"
            payment.provider_trade_no = parsed.get("provider_trade_no")
            payment.paid_at = now_local()
            # 不长期存完整 raw；仅存摘要
            payment.callback_raw = f"receipt={receipt}"
            order.payment_status = "PAID"
            order.status = "PAID"
            order.paid_at = now_local()
            order.payment_method = provider_u
            await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="CALLBACK")
            await record_order_status(db, order_id=order.id, from_status=old_o, to_status="PAID", source="PAYMENT")

        await db.flush()
        return payment

    @classmethod
    async def refund(
        cls,
        db: AsyncSession,
        payment: Payment,
        amount: Decimal,
        reason: str,
        operator_id: int | None = None,
    ) -> PaymentRefund:
        # 行锁，防止并发超退
        locked = (
            await db.execute(select(Payment).where(Payment.id == payment.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40401, "支付记录不存在")
        payment = locked
        if payment.status not in ("PAID", "PARTIAL_REFUND"):
            raise AppException(40050, "仅已支付可退款")
        amount = money(amount)
        if amount <= 0:
            raise AppException(40051, "退款金额非法")

        refunded = await db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("SUCCESS", "PENDING", "PROCESSING")),
            )
        ) or Decimal("0")
        if money(Decimal(str(refunded)) + amount) > money(payment.amount):
            raise AppException(40052, "退款总额超过支付金额")

        refund_no = generate_no("RF")
        row = PaymentRefund(
            refund_no=refund_no,
            payment_id=payment.id,
            order_id=payment.order_id,
            amount=amount,
            reason=reason,
            status="PENDING",
        )
        db.add(row)
        await db.flush()

        provider_inst = cls.get_provider(payment.provider)
        result = await provider_inst.refund_payment(payment, amount, reason, refund_no)
        row.status = result.get("status") or "PROCESSING"
        row.provider_refund_no = result.get("provider_refund_no")
        if row.status == "SUCCESS":
            old_status = payment.status
            payment.status = "REFUNDED" if money(Decimal(str(refunded)) + amount) >= money(payment.amount) else "PARTIAL_REFUND"
            await record_payment_status(
                db,
                payment_id=payment.id,
                from_status=old_status,
                to_status=payment.status,
                source="REFUND",
                remark=f"operator={operator_id}",
            )
        await db.flush()
        return row
