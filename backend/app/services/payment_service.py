"""支付服务：真实创建/退款 + 验签回调 + 金额/商户校验 + 防重放 + 状态机"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import Order, Payment, PaymentCallbackReceipt, PaymentRefund
from app.services import alipay_client, wechat_pay_client
from app.services.order_state_machine import assert_payment_transition
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.status_history import record_order_status, record_payment_status
from app.services.webhook_replay import claim_once, make_receipt_key

logger = logging.getLogger(__name__)


def _wechat_pay_configured() -> bool:
    s = get_settings()
    return bool(s.wechat_mch_id and s.wechat_api_v3_key and s.wechat_cert_serial_no)


def _alipay_pay_configured() -> bool:
    s = get_settings()
    return bool(s.alipay_app_id and s.alipay_private_key)


def _payment_mock_allowed(*, configured: bool) -> bool:
    """生产禁止 mock；开发显式 ALLOW_PAYMENT_MOCK=true 或未配置支付密钥时允许。"""
    s = get_settings()
    if s.is_production:
        return False
    if s.allow_payment_mock:
        return True
    return s.is_development and not configured


class WechatPayProvider:
    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        settings = get_settings()
        if not _wechat_pay_configured():
            if _payment_mock_allowed(configured=False):
                logger.warning("微信支付 MOCK 下单（仅开发）")
                return {
                    "provider": "WECHAT",
                    "payment_no": payment.payment_no,
                    "prepay_id": f"wx_mock_{payment.payment_no}",
                    "code_url": f"weixin://wxpay/bizpayurl?pr={payment.payment_no}",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            raise AppException(
                50011,
                "微信支付未配置，拒绝创建支付（开发可在 .env 设 ALLOW_PAYMENT_MOCK=true，或未配商户号时自动 mock）",
            )

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
        if not _wechat_pay_configured():
            if _payment_mock_allowed(configured=False):
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
            "is_refund": bool(
                data.get("out_refund_no")
                or str(data.get("event_type") or "").upper().startswith("REFUND")
                or data.get("refund_status")
            ),
            "refund_no": data.get("out_refund_no"),
            "provider_refund_no": data.get("refund_id"),
            "refund_status": data.get("refund_status"),
            "refund_amount": (
                data.get("amount", {}).get("refund")
                if isinstance(data.get("amount"), dict)
                else None
            ),
        }


class AlipayProvider:
    async def create_payment(self, payment: Payment, order: Order, extra: dict | None = None) -> dict:
        if not _alipay_pay_configured():
            if _payment_mock_allowed(configured=False):
                logger.warning("支付宝 MOCK 下单（仅开发）")
                return {
                    "provider": "ALIPAY",
                    "payment_no": payment.payment_no,
                    "qr_code": f"https://qr.alipay.com/{payment.payment_no}",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            raise AppException(
                50020,
                "支付宝未配置，拒绝创建支付（开发可在 .env 设 ALLOW_PAYMENT_MOCK=true，或未配密钥时自动 mock）",
            )
        return await alipay_client.trade_precreate(
            payment_no=payment.payment_no,
            amount_yuan=payment.amount,
            subject=f"订单{order.order_no}",
        )

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str, refund_no: str) -> dict:
        if not _alipay_pay_configured():
            if _payment_mock_allowed(configured=False):
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
        # 退款异步：有 refund_fee / gmt_refund；用 out_biz_no 对齐 out_request_no
        is_refund = bool(data.get("refund_fee") or data.get("gmt_refund"))
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("trade_no"),
            "amount": data.get("total_amount", "0"),
            "status": "PAID" if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED") else "FAILED",
            "app_id": data.get("app_id"),
            "is_refund": is_refund,
            "refund_no": data.get("out_biz_no") or data.get("out_request_no"),
            "provider_refund_no": data.get("trade_no"),
            "refund_status": "SUCCESS" if is_refund else None,
            "refund_fee": data.get("refund_fee"),
            "trade_status": trade_status,
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
        if pay_data.get("_mock"):
            await cls._settle_mock_payment(db, payment, order, provider.upper())
            pay_data["status"] = "PAID"
            pay_data["mock_settled"] = True
        return payment, pay_data

    @classmethod
    async def _settle_mock_payment(
        cls,
        db: AsyncSession,
        payment: Payment,
        order: Order,
        provider: str,
    ) -> None:
        """开发 mock 支付：跳过真实收银台，直接标记订单已支付。"""
        if payment.status == "PAID":
            return
        assert_payment_transition(order.status, "PAID")
        old_p = payment.status
        old_o = order.status
        payment.status = "PAID"
        payment.provider_trade_no = f"mock_{payment.payment_no}"
        payment.paid_at = now_local()
        payment.callback_raw = {"source": "MOCK", "settled": True}
        order.payment_status = "PAID"
        order.status = "PAID"
        order.paid_at = now_local()
        order.payment_method = provider
        await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="MOCK")
        await record_order_status(db, order_id=order.id, from_status=old_o, to_status="PAID", source="MOCK_PAYMENT")
        await db.flush()
        logger.warning("MOCK 支付已自动入账 order=%s payment=%s", order.order_no, payment.payment_no)

    @classmethod
    async def handle_callback(
        cls,
        db: AsyncSession,
        provider: str,
        headers: dict,
        body: bytes,
    ) -> Payment | PaymentRefund | None:
        provider_u = provider.upper()
        provider_inst = cls.get_provider(provider_u)

        if provider_u == "WECHAT":
            data = WechatPayVerifier().verify_and_parse(headers, body)
            parsed_probe = await provider_inst.handle_callback(data)
            if parsed_probe.get("is_refund"):
                receipt = make_receipt_key(
                    f"{provider_u}:REFUND",
                    headers.get("wechatpay-nonce") or headers.get("Wechatpay-Nonce") or "",
                    str(parsed_probe.get("refund_no") or ""),
                    str(parsed_probe.get("provider_refund_no") or ""),
                )
            else:
                receipt = make_receipt_key(
                    provider_u,
                    headers.get("wechatpay-nonce") or headers.get("Wechatpay-Nonce") or "",
                    data.get("out_trade_no") or "",
                    data.get("transaction_id") or "",
                )
        else:
            data = AlipayVerifier().verify_and_parse(headers, body)
            parsed_probe = await provider_inst.handle_callback(data)
            if parsed_probe.get("is_refund"):
                receipt = make_receipt_key(
                    f"{provider_u}:REFUND",
                    str(data.get("notify_id") or ""),
                    str(parsed_probe.get("refund_no") or data.get("out_trade_no") or ""),
                    str(data.get("gmt_refund") or data.get("refund_fee") or ""),
                )
            else:
                receipt = make_receipt_key(
                    provider_u,
                    str(data.get("notify_id") or ""),
                    str(data.get("out_trade_no") or ""),
                    str(data.get("trade_no") or ""),
                )

        first = await claim_once(f"pay:{provider_u}", receipt)
        if not first:
            if parsed_probe.get("is_refund"):
                refund_no = parsed_probe.get("refund_no")
                if refund_no:
                    return (
                        await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))
                    ).scalar_one_or_none()
                return None
            result = await db.execute(select(Payment).where(Payment.payment_no == parsed_probe.get("payment_no")))
            return result.scalar_one_or_none()

        try:
            db.add(
                PaymentCallbackReceipt(
                    provider=f"{provider_u}:REFUND" if parsed_probe.get("is_refund") else provider_u,
                    receipt_key=receipt,
                    payment_no=data.get("out_trade_no"),
                )
            )
            await db.flush()
        except Exception:
            await db.rollback()
            if parsed_probe.get("is_refund"):
                refund_no = parsed_probe.get("refund_no")
                if refund_no:
                    return (
                        await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))
                    ).scalar_one_or_none()
                return None
            result = await db.execute(select(Payment).where(Payment.payment_no == parsed_probe.get("payment_no")))
            return result.scalar_one_or_none()

        if parsed_probe.get("is_refund"):
            return await cls._apply_refund_callback(db, provider_u, data, parsed_probe, receipt)

        return await cls._apply_payment_callback(db, provider_u, provider_inst, data, receipt)

    @classmethod
    async def _apply_payment_callback(
        cls,
        db: AsyncSession,
        provider_u: str,
        provider_inst,
        data: dict,
        receipt: str,
    ) -> Payment | None:
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
        if order.status == "CANCELLED" or order.payment_status == "CANCELLED":
            raise AppException(40901, "订单已关闭，拒绝支付入账")

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
            payment.callback_raw = {"receipt": receipt}
            order.payment_status = "PAID"
            order.status = "PAID"
            order.paid_at = now_local()
            order.payment_method = provider_u
            await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="CALLBACK")
            await record_order_status(db, order_id=order.id, from_status=old_o, to_status="PAID", source="PAYMENT")

        await db.flush()
        return payment

    @classmethod
    async def _apply_refund_callback(
        cls,
        db: AsyncSession,
        provider_u: str,
        data: dict,
        parsed: dict,
        receipt: str,
    ) -> PaymentRefund | None:
        refund_no = parsed.get("refund_no")
        # 支付宝全额退款未传 out_request_no 时 out_biz_no 可能等于 out_trade_no
        if not refund_no or refund_no == parsed.get("payment_no"):
            # 回退：按支付单找最近一笔进行中的退款
            payment_no = parsed.get("payment_no")
            payment = (
                await db.execute(select(Payment).where(Payment.payment_no == payment_no))
            ).scalar_one_or_none()
            if not payment:
                raise AppException(40401, "支付记录不存在")
            row = (
                await db.execute(
                    select(PaymentRefund)
                    .where(
                        PaymentRefund.payment_id == payment.id,
                        PaymentRefund.status.in_(("PENDING", "PROCESSING")),
                    )
                    .order_by(PaymentRefund.id.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        else:
            row = (
                await db.execute(
                    select(PaymentRefund).where(PaymentRefund.refund_no == refund_no).with_for_update()
                )
            ).scalar_one_or_none()

        if not row:
            raise AppException(40403, "退款记录不存在")

        status_raw = str(parsed.get("refund_status") or "").upper()
        if provider_u == "WECHAT":
            if status_raw == "SUCCESS" or str(data.get("event_type") or "").upper() == "REFUND.SUCCESS":
                new_status = "SUCCESS"
            elif status_raw in ("CLOSED", "ABNORMAL") or "CLOSED" in str(data.get("event_type") or "").upper():
                new_status = "CLOSED" if "CLOSED" in status_raw or "CLOSED" in str(data.get("event_type") or "").upper() else "ABNORMAL"
            else:
                new_status = "PROCESSING"
        else:
            # 支付宝：带退款字段的异步即视为该笔退款成功（仍以商户退款单对齐）
            new_status = "SUCCESS"

        return await cls._finalize_refund(
            db,
            row,
            new_status,
            provider_refund_no=parsed.get("provider_refund_no"),
            callback_raw={"receipt": receipt, "parsed": {k: parsed.get(k) for k in (
                "refund_no", "refund_status", "payment_no", "provider_refund_no", "refund_fee", "event_type"
            )}},
            source="CALLBACK",
        )

    @classmethod
    async def _finalize_refund(
        cls,
        db: AsyncSession,
        row: PaymentRefund,
        new_status: str,
        *,
        provider_refund_no: str | None = None,
        callback_raw: dict | None = None,
        fail_reason: str | None = None,
        source: str = "SYSTEM",
    ) -> PaymentRefund:
        if row.status == "SUCCESS":
            return row

        if provider_refund_no:
            row.provider_refund_no = provider_refund_no
        if callback_raw is not None:
            row.callback_raw = callback_raw
        if fail_reason:
            row.fail_reason = fail_reason[:255]

        if new_status == "SUCCESS":
            row.status = "SUCCESS"
            row.succeeded_at = now_local()
            payment = (
                await db.execute(select(Payment).where(Payment.id == row.payment_id).with_for_update())
            ).scalar_one_or_none()
            if not payment:
                raise AppException(40401, "支付记录不存在")
            success_sum = await db.scalar(
                select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                    PaymentRefund.payment_id == payment.id,
                    PaymentRefund.status == "SUCCESS",
                )
            ) or Decimal("0")
            # 当前行刚置 SUCCESS，sum 已包含
            old_status = payment.status
            payment.status = (
                "REFUNDED" if money(Decimal(str(success_sum))) >= money(payment.amount) else "PARTIAL_REFUND"
            )
            await record_payment_status(
                db,
                payment_id=payment.id,
                from_status=old_status,
                to_status=payment.status,
                source=source,
                remark=f"refund_no={row.refund_no}",
            )
            order = (
                await db.execute(select(Order).where(Order.id == row.order_id).with_for_update())
            ).scalar_one_or_none()
            if order:
                order.payment_status = payment.status
                if payment.status == "REFUNDED" and order.status in ("PAID", "CONFIRMED"):
                    old_o = order.status
                    order.status = "REFUNDED"
                    await record_order_status(
                        db, order_id=order.id, from_status=old_o, to_status="REFUNDED", source=source
                    )
        elif new_status in ("CLOSED", "ABNORMAL", "FAILED"):
            row.status = new_status if new_status != "ABNORMAL" else "FAILED"
            row.fail_reason = row.fail_reason or fail_reason or new_status
        else:
            row.status = "PROCESSING"

        await db.flush()
        return row

    @classmethod
    async def refundable_amount(cls, db: AsyncSession, payment: Payment) -> Decimal:
        """可退余额 = 支付金额 − (成功+进行中)退款。"""
        used = await db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("SUCCESS", "PENDING", "PROCESSING")),
            )
        ) or Decimal("0")
        left = money(payment.amount) - money(Decimal(str(used)))
        return left if left > 0 else money(Decimal("0"))

    @classmethod
    async def refund(
        cls,
        db: AsyncSession,
        payment: Payment,
        amount: Decimal,
        reason: str,
        operator_id: int | None = None,
    ) -> PaymentRefund:
        locked = (
            await db.execute(select(Payment).where(Payment.id == payment.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40401, "支付记录不存在")
        payment = locked
        if payment.status == "REFUNDED":
            raise AppException(40053, "该支付已全额退款，不可重复退款")
        if payment.status not in ("PAID", "PARTIAL_REFUND"):
            raise AppException(40050, "仅已支付可退款")
        amount = money(amount)
        if amount <= 0:
            raise AppException(40051, "退款金额非法")

        # 进行中的退款单：禁止再开一笔，避免连点重复提交
        inflight = await db.scalar(
            select(func.count()).select_from(PaymentRefund).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("PENDING", "PROCESSING")),
            )
        ) or 0
        if int(inflight) > 0:
            raise AppException(40054, "已有退款处理中，请勿重复提交")

        refunded_success = await db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status == "SUCCESS",
            )
        ) or Decimal("0")
        remain = money(payment.amount) - money(Decimal(str(refunded_success)))
        if remain <= 0:
            raise AppException(40053, "可退余额为 0，不可再退")
        if amount > remain:
            raise AppException(40052, f"退款金额超过可退余额 ¥{remain}")

        refund_no = generate_no("RF")
        row = PaymentRefund(
            refund_no=refund_no,
            payment_id=payment.id,
            order_id=payment.order_id,
            amount=amount,
            reason=reason,
            status="PENDING",
            operator_id=operator_id,
        )
        db.add(row)
        await db.flush()

        provider_inst = cls.get_provider(payment.provider)
        try:
            result = await provider_inst.refund_payment(payment, amount, reason, refund_no)
        except Exception as e:
            row.status = "FAILED"
            row.fail_reason = str(e)[:255]
            await db.flush()
            raise

        api_status = (result.get("status") or "PROCESSING").upper()
        row.provider_refund_no = result.get("provider_refund_no")
        if result.get("raw"):
            row.callback_raw = {"apply": result.get("raw")}

        if result.get("_mock") or api_status == "SUCCESS":
            await cls._finalize_refund(
                db,
                row,
                "SUCCESS",
                provider_refund_no=result.get("provider_refund_no"),
                callback_raw=row.callback_raw,
                source="REFUND_APPLY" if not result.get("_mock") else "MOCK",
            )
        elif api_status in ("CLOSED", "ABNORMAL", "FAILED"):
            await cls._finalize_refund(
                db,
                row,
                api_status,
                provider_refund_no=result.get("provider_refund_no"),
                fail_reason=api_status,
                source="REFUND_APPLY",
            )
        else:
            # 受理成功，等待回调/定时查询
            row.status = "PROCESSING"
            await db.flush()

        return row

    @classmethod
    async def sync_refund_status(cls, db: AsyncSession, row: PaymentRefund) -> PaymentRefund:
        """主动查询平台退款结果（补偿回调丢失）。"""
        if row.status == "SUCCESS":
            return row
        payment = await db.get(Payment, row.payment_id)
        if not payment:
            raise AppException(40401, "支付记录不存在")

        if payment.provider == "WECHAT":
            if not _wechat_pay_configured():
                return row
            result = await wechat_pay_client.query_refund(refund_no=row.refund_no)
        elif payment.provider == "ALIPAY":
            if not _alipay_pay_configured():
                return row
            result = await alipay_client.trade_refund_query(
                payment_no=payment.payment_no, refund_no=row.refund_no
            )
        else:
            return row

        return await cls._finalize_refund(
            db,
            row,
            result.get("status") or "PROCESSING",
            provider_refund_no=result.get("provider_refund_no"),
            callback_raw={"query": result.get("raw")},
            source="REFUND_QUERY",
        )

    @classmethod
    async def poll_pending_refunds(cls, limit: int = 20) -> int:
        """定时补偿：拉取 PROCESSING/PENDING 退款单。"""
        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(PaymentRefund)
                        .where(PaymentRefund.status.in_(("PENDING", "PROCESSING")))
                        .order_by(PaymentRefund.id.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            done = 0
            for row in rows:
                try:
                    before = row.status
                    await cls.sync_refund_status(db, row)
                    if row.status != before and row.status == "SUCCESS":
                        done += 1
                except Exception as e:
                    logger.warning("退款查询失败 refund_no=%s: %s", row.refund_no, e)
            await db.commit()
            return done
