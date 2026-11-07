"""支付服务：真实创建/退款 + 验签回调 + 金额/商户校验 + 防重放 + 状态机"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.biz_no import generate_no, payment_biz, refund_biz, resolve_table_parts
from app.core.exceptions import AppException
from app.core.response import money, now_local, serialize_dt
from app.models import (
    Customer,
    DiningTable,
    Order,
    Payment,
    PaymentCallbackReceipt,
    PaymentRefund,
    PosRefundRequest,
    RechargeOrder,
    Store,
)
from app.services import alipay_client, wechat_pay_client
from app.services.customer_service import CustomerService
from app.services.order_lifecycle import apply_order_status
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.status_history import record_payment_status
from app.services.webhook_replay import claim_once, make_receipt_key, release_claim

logger = logging.getLogger(__name__)


def _wechat_callback_receipt_identity(data: dict, parsed: dict) -> tuple[str, ...]:
    """构造稳定的微信通知身份；签名头 nonce 是每次 HTTP 请求随机值，不能用于回调幂等。"""
    notification_id = str(data.get("id") or "").strip()
    if parsed.get("is_refund"):
        return (
            f"{notification_id}" if notification_id else "",
            str(parsed.get("refund_no") or data.get("out_refund_no") or "").strip(),
            str(data.get("event_type") or parsed.get("refund_status") or "").strip().upper(),
            str(data.get("success_time") or data.get("create_time") or "").strip(),
        )
    return (
        notification_id,
        str(data.get("out_trade_no") or parsed.get("payment_no") or "").strip(),
        str(data.get("transaction_id") or parsed.get("provider_trade_no") or "").strip(),
        str(data.get("event_type") or parsed.get("status") or "").strip().upper(),
    )


def _validate_duplicate_paid_callback(provider_u: str, payment: Payment, parsed: dict) -> None:
    """已支付记录的重复成功回调必须与原渠道、原金额、原交易号完全一致。"""
    provider = str(provider_u or "").strip().upper()
    stored_provider = str(getattr(payment, "provider", "") or "").strip().upper()
    if provider not in ("WECHAT", "ALIPAY") or stored_provider != provider:
        raise AppException(40302, "支付回调渠道与本地支付记录不匹配")

    raw_amount = parsed.get("amount")
    if isinstance(raw_amount, dict):
        raw_amount = raw_amount.get("total")
    if raw_amount is None:
        raise AppException(40302, "重复支付回调缺少金额")
    try:
        callback_amount = (
            money(Decimal(str(raw_amount)) / 100)
            if provider == "WECHAT"
            else money(Decimal(str(raw_amount)))
        )
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AppException(40302, "重复支付回调金额非法") from exc
    if callback_amount != money(payment.amount):
        raise AppException(40302, "重复支付回调金额与原支付记录不匹配")

    callback_trade_no = str(parsed.get("provider_trade_no") or "").strip()
    stored_trade_no = str(getattr(payment, "provider_trade_no", "") or "").strip()
    if not callback_trade_no or not stored_trade_no or callback_trade_no != stored_trade_no:
        raise AppException(40302, "重复支付回调渠道交易号与原支付记录不匹配")


def _validate_refund_callback_binding(
    provider: str,
    parsed: dict,
    refund: PaymentRefund,
    payment: Payment | None,
) -> None:
    """Ensure a signed refund notification belongs to the exact local payment/refund pair."""
    if payment is None:
        raise AppException(40401, "退款关联的支付记录不存在")
    expected_provider = str(payment.provider or "").strip().upper()
    if expected_provider != str(provider or "").strip().upper():
        raise AppException(40302, "退款回调支付渠道与本地支付记录不匹配")
    callback_payment_no = str(parsed.get("payment_no") or "").strip()
    if not callback_payment_no or callback_payment_no != str(payment.payment_no or "").strip():
        raise AppException(40302, "退款回调原支付单号与本地记录不匹配")
    if int(refund.payment_id) != int(payment.id):
        raise AppException(40302, "退款单与原支付记录关联不一致")
    refund_order_id = getattr(refund, "order_id", None)
    payment_order_id = getattr(payment, "order_id", None)
    # 订单支付必须绑定同一订单；余额充值等非订单支付允许双方 order_id 同时为空。
    if (refund_order_id is None) != (payment_order_id is None):
        raise AppException(40302, "退款单与原支付单订单关联不一致")
    if refund_order_id is not None and int(refund_order_id) != int(payment_order_id):
        raise AppException(40302, "退款单与原支付单订单关联不一致")
    # 微信退款通知 transaction_id、支付宝异步通知 trade_no 均用于标识原渠道交易。
    # 必须与本地已保存的 provider_trade_no 绑定，不能只依赖商户侧 payment_no。
    callback_trade_no = str(parsed.get("provider_trade_no") or "").strip()
    local_trade_no = str(payment.provider_trade_no or "").strip()
    if not callback_trade_no or not local_trade_no or callback_trade_no != local_trade_no:
        raise AppException(40302, "退款回调渠道原交易号与本地支付记录不匹配")
    callback_refund_no = str(parsed.get("refund_no") or "").strip()
    if not callback_refund_no:
        raise AppException(40302, "退款回调缺少商户退款单号")
    if callback_refund_no != str(refund.refund_no or "").strip():
        raise AppException(40302, "渠道退款单号与本地退款单不匹配")


async def _order_table_parts(db: AsyncSession, order: Order) -> tuple[str | None, str]:
    table_no = None
    if order.table_id:
        t = await db.get(DiningTable, order.table_id)
        if t:
            table_no = t.table_no
    return resolve_table_parts(table_no, channel=order.channel, dining_type=order.dining_type)


async def _order_store_no(db: AsyncSession, order: Order) -> str | None:
    if not order.store_id:
        return None
    store = await db.get(Store, order.store_id)
    return store.store_no if store else None


def _wechat_pay_configured() -> bool:
    """与 wechat_pay_client._configured 一致：APIv3 签名需要证书序列号。"""
    s = get_settings()
    return bool(s.wechat_mch_id and s.wechat_api_v3_key and s.wechat_cert_serial_no and s.wechat_app_id)


def _wechat_micropay_configured() -> bool:
    """付款码走 V2，只需商户号 / AppID / API 密钥（无证书序列号）。"""
    s = get_settings()
    return bool(s.wechat_mch_id and s.wechat_api_v3_key and s.wechat_app_id)


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
        channel = (payment.channel or "").upper()
        desc = f"订单{order.order_no}"
        auth_code = (extra or {}).get("auth_code")
        is_mini = "MINIPROGRAM" in channel or "JSAPI" in channel
        is_micropay = "MICROPAY" in channel
        configured = _wechat_micropay_configured() if is_micropay else _wechat_pay_configured()
        # 显式 ALLOW_PAYMENT_MOCK：即使已配商户号也跳过真实网关（未开通支付产品时可联调）
        if configured and _payment_mock_allowed(configured=True):
            logger.warning(
                "微信支付 MOCK（ALLOW_PAYMENT_MOCK=true） payment=%s channel=%s",
                payment.payment_no,
                channel,
            )
            if is_micropay or auth_code:
                return {
                    "provider": "WECHAT",
                    "payment_no": payment.payment_no,
                    "trade_state": "SUCCESS",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            return {
                "provider": "WECHAT",
                "payment_no": payment.payment_no,
                "prepay_id": f"wx_mock_{payment.payment_no}",
                "code_url": f"weixin://wxpay/bizpayurl?pr={payment.payment_no}",
                "amount": str(payment.amount),
                "_mock": True,
            }
        if is_micropay:
            if not auth_code:
                raise AppException(40041, "请扫描顾客微信付款码")
            if not _wechat_micropay_configured():
                if _payment_mock_allowed(configured=False):
                    logger.warning("微信付款码 MOCK（仅开发）")
                    return {
                        "provider": "WECHAT",
                        "payment_no": payment.payment_no,
                        "trade_state": "SUCCESS",
                        "amount": str(payment.amount),
                        "_mock": True,
                    }
                raise AppException(50011, "微信支付未配置")
            from app.services.barcode_pay_service import settle_wechat_barcode

            data = await settle_wechat_barcode(
                payment_no=payment.payment_no,
                description=desc,
                amount_yuan=payment.amount,
                auth_code=str(auth_code),
            )
            data["amount"] = str(payment.amount)
            return data

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

        if is_mini:
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
        configured = _wechat_pay_configured()
        if configured and _payment_mock_allowed(configured=True):
            return {"refund_no": refund_no, "status": "SUCCESS", "amount": str(amount), "_mock": True}
        if not configured:
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
        amt = data.get("amount")
        currency = None
        if isinstance(amt, dict):
            currency = amt.get("currency")
            total = amt.get("total", 0)
        else:
            total = amt or 0
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("transaction_id"),
            # 保留微信原始 amount 对象（total/refund/currency/payer_total）。
            # 支付回调处理器读取 total；退款处理器还必须读取 refund 和 currency。
            "amount": dict(amt) if isinstance(amt, dict) else str(total),
            "currency": currency,
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
        channel = (payment.channel or "").upper()
        auth_code = (extra or {}).get("auth_code")
        if "BARCODE" in channel and not auth_code:
            raise AppException(40041, "请扫描顾客支付宝付款码")
        is_mini = "MINIAPP" in channel or "MINIPROGRAM" in channel or "JSAPI" in channel
        configured = _alipay_pay_configured()
        # 显式 ALLOW_PAYMENT_MOCK：即使已配密钥也跳过真实网关（JSAPI 未签约时可联调）
        if configured and _payment_mock_allowed(configured=True):
            logger.warning(
                "支付宝支付 MOCK（ALLOW_PAYMENT_MOCK=true） payment=%s channel=%s",
                payment.payment_no,
                channel,
            )
            if "BARCODE" in channel or auth_code:
                return {
                    "provider": "ALIPAY",
                    "payment_no": payment.payment_no,
                    "trade_status": "TRADE_SUCCESS",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            if is_mini:
                return {
                    "provider": "ALIPAY",
                    "payment_no": payment.payment_no,
                    "trade_no": f"mock_ali_{payment.payment_no}",
                    "amount": str(payment.amount),
                    "_mock": True,
                }
            return {
                "provider": "ALIPAY",
                "payment_no": payment.payment_no,
                "qr_code": f"https://qr.alipay.com/{payment.payment_no}",
                "amount": str(payment.amount),
                "_mock": True,
            }
        if not configured:
            if _payment_mock_allowed(configured=False):
                if auth_code:
                    logger.warning("支付宝付款码 MOCK（仅开发）")
                    return {
                        "provider": "ALIPAY",
                        "payment_no": payment.payment_no,
                        "trade_status": "TRADE_SUCCESS",
                        "amount": str(payment.amount),
                        "_mock": True,
                    }
                logger.warning("支付宝 MOCK 下单（仅开发）")
                if is_mini:
                    return {
                        "provider": "ALIPAY",
                        "payment_no": payment.payment_no,
                        "trade_no": f"mock_ali_{payment.payment_no}",
                        "amount": str(payment.amount),
                        "_mock": True,
                    }
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
        if "BARCODE" in channel:
            from app.services.barcode_pay_service import settle_alipay_barcode

            data = await settle_alipay_barcode(
                payment_no=payment.payment_no,
                amount_yuan=payment.amount,
                subject=f"订单{order.order_no}",
                auth_code=str(auth_code),
            )
            return data
        # 小程序内必须用 JSAPI（alipay.trade.create + my.tradePay），不能用当面付预下单
        if is_mini:
            buyer_id = str((extra or {}).get("buyer_id") or "").strip()
            if not buyer_id:
                raise AppException(40042, "小程序支付缺少买家支付宝用户号，请重新登录后再支付")
            if buyer_id.startswith("ali_dev_"):
                raise AppException(
                    40043,
                    "当前为开发 Mock 买家，无法调真实支付宝支付；请用真机授权登录，或设 ALLOW_PAYMENT_MOCK=true",
                )
            from app.core.config import get_settings

            settings = get_settings()
            return await alipay_client.trade_create(
                payment_no=payment.payment_no,
                amount_yuan=payment.amount,
                subject=f"订单{order.order_no}",
                buyer_id=buyer_id,
                op_app_id=settings.alipay_app_id or None,
            )
        return await alipay_client.trade_precreate(
            payment_no=payment.payment_no,
            amount_yuan=payment.amount,
            subject=f"订单{order.order_no}",
        )

    async def refund_payment(self, payment: Payment, amount: Decimal, reason: str, refund_no: str) -> dict:
        configured = _alipay_pay_configured()
        if configured and _payment_mock_allowed(configured=True):
            return {"refund_no": refund_no, "status": "SUCCESS", "amount": str(amount), "_mock": True}
        if not configured:
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
        # 退款通知必须保留支付宝原始 refund_status；不能仅凭 refund_fee 推断退款成功。
        # refund_status / gmt_refund 均属于支付宝异步通知字段。
        is_refund = bool(
            data.get("refund_status") or data.get("refund_fee") or data.get("gmt_refund")
        )
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("trade_no"),
            "amount": str(data.get("total_amount", 0)),  # 支付宝回调金额单位：元
            "currency": data.get("currency") or "CNY",
            "status": "PAID" if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED") else "FAILED",
            "app_id": data.get("app_id"),
            "is_refund": is_refund,
            "refund_no": data.get("out_biz_no") or data.get("out_request_no"),
            # 支付宝 trade_no 是原支付交易号，不是独立退款单号；不可写入 provider_refund_no。
            "provider_refund_no": None,
            # Alipay refund_fee is the transaction's cumulative successful refund total,
            # not the amount of this individual out_request_no.
            "refund_status": data.get("refund_status"),
            "fund_change": data.get("fund_change"),
            "gmt_refund": data.get("gmt_refund"),
            "refund_fee": data.get("refund_fee"),
            "trade_status": trade_status,
        }


def _resolve_alipay_refund_status(parsed: dict) -> str:
    """仅按支付宝明确退款状态或退款时间确认成功；退款金额字段本身不是成功凭据。"""
    status = str(parsed.get("refund_status") or "").strip().upper()
    if status in {"REFUND_SUCCESS", "SUCCESS"}:
        return "SUCCESS"
    if status in {"REFUND_CLOSED", "CLOSED"}:
        return "CLOSED"
    if status in {"SELLER_REFUSE_BUYER", "FAILED"}:
        return "FAILED"
    # 等待卖家同意、买家退货、卖家确认收货等状态都不是退款成功。
    if status:
        return "PROCESSING"
    # 部分旧版通知可能只提供退款时间；时间戳可作为已发生退款的证据。
    if parsed.get("gmt_refund"):
        return "SUCCESS"
    # refund_fee 仅表示金额，不足以证明退款状态已成功。
    return "PROCESSING"


class PaymentService:
    PROVIDERS = {"WECHAT": WechatPayProvider, "ALIPAY": AlipayProvider}
    CHANNEL_MAP = {
        ("WECHAT", "h5"): "WECHAT_H5",
        ("WECHAT", "native"): "WECHAT_NATIVE",
        ("WECHAT", "micropay"): "WECHAT_MICROPAY",
        ("WECHAT", "miniprogram"): "WECHAT_MINIPROGRAM",
        ("ALIPAY", "qr"): "ALIPAY_QR",
        ("ALIPAY", "barcode"): "ALIPAY_BARCODE",
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
    async def order_net_collected(cls, db: AsyncSession, order_id: int) -> Decimal:
        """订单净已收：各支付单金额 − 成功退款（含 PAID/PARTIAL_REFUND/REFUNDED）。"""
        payments = list(
            (
                await db.execute(
                    select(Payment).where(
                        Payment.order_id == int(order_id),
                        Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                    )
                )
            ).scalars().all()
        )
        if not payments:
            return money(0)
        pids = [p.id for p in payments]
        refund_rows = (
            await db.execute(
                select(PaymentRefund.payment_id, func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                    PaymentRefund.payment_id.in_(pids),
                    PaymentRefund.status == "SUCCESS",
                ).group_by(PaymentRefund.payment_id)
            )
        ).all()
        refund_map = {int(pid): money(amt or 0) for pid, amt in refund_rows}
        net = money(0)
        for p in payments:
            left = money(p.amount) - refund_map.get(int(p.id), money(0))
            if left > 0:
                net += left
        return money(net)

    @classmethod
    async def paid_amount(cls, db: AsyncSession, order_id: int) -> Decimal:
        """订单净已收金额（已入账支付 − 成功退款）。

        不得只统计 status=PAID：部分退款后支付单变为 PARTIAL_REFUND，
        若仍按 PAID 合计会导致 remaining 虚高、可重复收款。
        """
        return await cls.order_net_collected(db, order_id)

    @classmethod
    async def remaining_amount(cls, db: AsyncSession, order: Order) -> Decimal:
        # 全额退款终态：禁止再收
        if str(getattr(order, "payment_status", "") or "").upper() == "REFUNDED":
            return money(0)
        if str(getattr(order, "status", "") or "").upper() == "REFUNDED":
            return money(0)
        rem = money(order.total_amount) - await cls.paid_amount(db, order.id)
        return rem if rem > 0 else money(0)

    @classmethod
    def _tender_dict(cls, p: Payment) -> dict:
        return {
            "payment_no": p.payment_no,
            "provider": p.provider,
            "channel": p.channel,
            "amount": str(money(p.amount)),
            "status": p.status,
            "paid_at": serialize_dt(p.paid_at) if p.paid_at else None,
        }

    @classmethod
    async def list_paid_tenders(cls, db: AsyncSession, order_id: int) -> list[dict]:
        rows = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.order_id == int(order_id),
                    Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                )
                .order_by(Payment.id.asc())
            )
        ).scalars().all()
        return [cls._tender_dict(p) for p in rows]

    @classmethod
    async def list_paid_tenders_with_refund(cls, db: AsyncSession, order_id: int) -> list[dict]:
        """订单详情用：收款行附带已退/可退/处理中，供后台直接退款。"""
        rows = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.order_id == int(order_id),
                    Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                )
                .order_by(Payment.id.asc())
            )
        ).scalars().all()
        if not rows:
            return []
        ids = [int(p.id) for p in rows]
        refunded_map: dict[int, Decimal] = {}
        inflight_map: dict[int, Decimal] = {}
        success_rows = await db.execute(
            select(PaymentRefund.payment_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
            .where(
                PaymentRefund.payment_id.in_(ids),
                PaymentRefund.status == "SUCCESS",
            )
            .group_by(PaymentRefund.payment_id)
        )
        for pid, amt in success_rows.all():
            refunded_map[int(pid)] = Decimal(str(amt))
        inflight_rows = await db.execute(
            select(PaymentRefund.payment_id, func.coalesce(func.sum(PaymentRefund.amount), 0))
            .where(
                PaymentRefund.payment_id.in_(ids),
                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
            )
            .group_by(PaymentRefund.payment_id)
        )
        for pid, amt in inflight_rows.all():
            inflight_map[int(pid)] = Decimal(str(amt))
        out: list[dict] = []
        for p in rows:
            refunded = refunded_map.get(int(p.id), Decimal("0"))
            inflight = inflight_map.get(int(p.id), Decimal("0"))
            remain = money(p.amount) - money(refunded) - money(inflight)
            if remain < 0:
                remain = money(Decimal("0"))
            item = cls._tender_dict(p)
            item.update(
                {
                    "id": int(p.id),
                    "order_id": int(p.order_id) if p.order_id else None,
                    "refunded_amount": str(money(refunded)),
                    "refundable_amount": str(remain),
                    "refund_inflight": str(money(inflight)),
                }
            )
            out.append(item)
        return out

    @classmethod
    async def list_paid_tenders_map(
        cls, db: AsyncSession, order_ids: list[int]
    ) -> dict[int, list[dict]]:
        """批量：order_id → 已收款项（列表页展示组合支付明细）。"""
        ids = sorted({int(x) for x in order_ids if x})
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.order_id.in_(ids),
                    Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                )
                .order_by(Payment.order_id.asc(), Payment.id.asc())
            )
        ).scalars().all()
        out: dict[int, list[dict]] = {i: [] for i in ids}
        for p in rows:
            out.setdefault(int(p.order_id), []).append(cls._tender_dict(p))
        return out

    @classmethod
    async def payment_progress(cls, db: AsyncSession, order: Order) -> dict:
        paid = await cls.paid_amount(db, order.id)
        remaining = await cls.remaining_amount(db, order)
        method = (order.payment_method or "").strip() or await cls._resolve_payment_method(db, order.id)
        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "total_amount": str(money(order.total_amount)),
            "paid_amount": str(paid),
            "remaining_amount": str(remaining),
            "payment_status": order.payment_status,
            "payment_method": method or None,
            "order_status": order.status,
            "fully_paid": remaining <= 0 and order.payment_status == "PAID",
            "tenders": await cls.list_paid_tenders(db, order.id),
        }

    @classmethod
    async def _resolve_payment_method(cls, db: AsyncSession, order_id: int) -> str:
        rows = (
            await db.execute(
                select(Payment.provider).where(
                    Payment.order_id == int(order_id),
                    Payment.status == "PAID",
                )
            )
        ).scalars().all()
        providers = {str(p or "").upper() for p in rows if p}
        if len(providers) > 1:
            return "MIXED"
        if len(providers) == 1:
            return next(iter(providers))
        return ""

    @classmethod
    async def _finalize_tender(
        cls,
        db: AsyncSession,
        *,
        payment: Payment,
        order: Order,
        provider: str,
        source: str,
    ) -> dict:
        """本笔支付单已标 PAID 后：未结清→PARTIAL_PAID；结清→订单 PAID。
        微信/支付宝/现金记 SALE；BALANCE 不记 SALE（充值时已记实收，避免重复营收）。
        """
        provider_u = (provider or payment.provider or "").upper()
        if provider_u != "BALANCE":
            from app.services.financial_ledger_service import FinancialLedgerService

            try:
                await FinancialLedgerService.record_sale(
                    db,
                    store_id=int(order.store_id),
                    amount=payment.amount,
                    channel=order.channel or payment.channel,
                    provider=provider or payment.provider,
                    order_id=order.id,
                    payment_id=payment.id,
                    remark=f"payment_no={payment.payment_no}",
                )
            except Exception as exc:
                logger.exception(
                    "财务分录 SALE 写入失败 payment_id=%s order_id=%s", payment.id, order.id
                )
                from app.core.config import get_settings

                if get_settings().is_production:
                    raise AppException(50010, "财务分录写入失败，支付未确认，请重试") from exc

        paid = await cls.paid_amount(db, order.id)
        remaining = money(order.total_amount) - paid
        method = await cls._resolve_payment_method(db, order.id)
        if remaining > 0:
            order.payment_status = "PARTIAL_PAID"
            order.payment_method = method
            await db.flush()
            return {
                "fully_paid": False,
                "paid_amount": str(paid),
                "remaining_amount": str(money(remaining)),
                "payment_status": "PARTIAL_PAID",
            }

        order.payment_method = method
        order.paid_at = now_local()
        await apply_order_status(
            db, order, "PAID", source=source, payment_status="PAID", assert_kind="PAYMENT"
        )
        await db.flush()
        return {
            "fully_paid": True,
            "paid_amount": str(paid),
            "remaining_amount": "0.00",
            "payment_status": "PAID",
        }

    @classmethod
    async def create_payment(
        cls,
        db: AsyncSession,
        order: Order,
        provider: str,
        channel_type: str = "native",
        customer_id: int | None = None,
        operator_id: int | None = None,
        extra: dict | None = None,
        amount: Decimal | None = None,
    ) -> tuple[Payment, dict]:
        locked = (
            await db.execute(select(Order).where(Order.id == order.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40402, "订单不存在")
        order = locked

        if order.payment_status == "PAID":
            raise AppException(40010, "订单已支付")
        if order.status == "CANCELLED":
            raise AppException(40011, "订单已取消")
        # P0 安全收敛：允许 PENDING_PAYMENT 与未支付的 CONFIRMED 发起支付。
        # CONFIRMED 路径用于异常恢复（外卖同步/历史数据等导致 CONFIRMED 但未入账）。
        if order.status not in ("PENDING_PAYMENT", "CONFIRMED"):
            raise AppException(40012, f"当前订单状态不可支付: {order.status}")

        remaining = await cls.remaining_amount(db, order)
        if remaining <= 0:
            raise AppException(40010, "订单已支付")

        pay_amount = money(amount) if amount is not None else remaining
        if pay_amount <= 0:
            raise AppException(40051, "收款金额非法")
        if pay_amount > remaining:
            raise AppException(40052, f"本笔金额超过待收余额 ¥{remaining}")
        logger.info(
            "创建支付单 order=%s provider=%s channel=%s pay_amount=%s remaining=%s total=%s",
            order.order_no,
            provider,
            channel_type,
            pay_amount,
            remaining,
            money(order.total_amount),
        )

        provider_u = provider.upper()
        if provider_u == "BALANCE":
            channel = "BALANCE"
        else:
            channel = cls.CHANNEL_MAP.get(
                (provider_u, channel_type.lower()),
                f"{provider_u}_{channel_type.upper()}",
            )

        # 同一订单任一时刻仅允许一个有效支付流程。订单行锁使检查与创建串行化；
        # 全额同渠道重试复用原支付单，跨渠道或分笔并发必须先确认原流程结果。
        pending_rows = list(
            (
                await db.execute(
                    select(Payment)
                    .where(Payment.order_id == order.id, Payment.status == "PENDING")
                    .order_by(Payment.id.desc())
                )
            ).scalars().all()
        )
        existing = None
        if pay_amount == remaining == money(order.total_amount):
            existing = next(
                (
                    row
                    for row in pending_rows
                    if row.provider == provider_u
                    and row.channel == channel
                    and money(row.amount) == pay_amount
                ),
                None,
            )
        conflicts = [row for row in pending_rows if existing is None or row.id != existing.id]
        if conflicts:
            raise AppException(
                40908,
                "该订单已有支付流程进行中，请先查单确认结果，避免微信/支付宝重复扣款",
                status_code=409,
            )

        reused = False
        if existing and money(existing.amount) == pay_amount:
            payment = existing
            reused = True
            if operator_id is not None:
                payment.operator_id = operator_id
            if customer_id is not None:
                payment.customer_id = customer_id
        else:
            t_no, t_ph = await _order_table_parts(db, order)
            sno = await _order_store_no(db, order)
            payment = Payment(
                payment_no=generate_no(payment_biz(provider_u), sno, t_no, table_placeholder=t_ph),
                order_id=order.id,
                customer_id=customer_id or order.customer_id,
                operator_id=operator_id,
                provider=provider_u,
                channel=channel,
                amount=pay_amount,
                status="PENDING",
            )
            db.add(payment)
            await db.flush()
            await record_payment_status(
                db, payment_id=payment.id, from_status=None, to_status="PENDING", source="CREATE"
            )

        # 余额支付：本事务内扣款并结清，不走第三方
        if provider_u == "BALANCE":
            cid = customer_id or order.customer_id
            if not cid:
                raise AppException(40040, "余额支付需绑定顾客")
            # 游客/匿名会话不得使用储值财产能力
            payer = await db.get(Customer, int(cid))
            if not payer or not CustomerService.is_member(payer):
                raise AppException(40320, "请先开通会员后再使用余额支付", status_code=403)
            # 储值已含赠送，禁止再叠会员折扣/券/代金券
            promo = (
                money(getattr(order, "discount_amount", 0) or 0)
                + money(getattr(order, "coupon_discount", 0) or 0)
                + money(getattr(order, "voucher_discount", 0) or 0)
            )
            if promo > 0 or getattr(order, "customer_coupon_id", None) or getattr(order, "customer_voucher_id", None):
                raise AppException(40053, "余额支付不可叠加会员折扣或优惠券，请重新下单")
            if money(pay_amount) != money(remaining):
                raise AppException(40052, "余额支付须一次结清待收金额")
            from app.services.balance_service import BalanceService

            try:
                await BalanceService.debit_for_order(
                    db,
                    customer_id=int(cid),
                    order_id=order.id,
                    amount=pay_amount,
                    store_id=order.store_id,
                    payment_id=payment.id,
                    order_no=getattr(order, "order_no", None),
                )
            except AppException:
                if payment.status == "PENDING":
                    old_fail = payment.status
                    payment.status = "CLOSED"
                    await record_payment_status(
                        db,
                        payment_id=payment.id,
                        from_status=old_fail,
                        to_status="CLOSED",
                        source="BALANCE",
                        remark="余额不足或扣款失败",
                    )
                    await db.flush()
                raise
            old_p = payment.status
            payment.status = "PAID"
            payment.provider_trade_no = f"bal_{payment.payment_no}"
            payment.paid_at = now_local()
            payment.customer_id = int(cid)
            await record_payment_status(
                db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="BALANCE"
            )
            progress = await cls._finalize_tender(
                db, payment=payment, order=order, provider="BALANCE", source="BALANCE"
            )
            return payment, {
                "provider": "BALANCE",
                "payment_no": payment.payment_no,
                "amount": str(payment.amount),
                "status": "PAID",
                "already_paid": False,
                **progress,
            }

        # 短事务：落库支付单并释放行锁后再调第三方，避免网络抖动拖垮连接池
        payment_id = payment.id
        order_id = order.id
        await db.commit()
        payment = await db.get(Payment, payment_id)
        order = await db.get(Order, order_id)
        if not payment or not order:
            raise AppException(40402, "订单/支付单不存在")

        # 复用未支付单：先向渠道查单（防回调丢失重复收款），再尝试用已存凭证重签
        if reused:
            try:
                synced, sync_meta = await cls.sync_payment_status(db, payment)
            except AppException as exc:
                logger.warning(
                    "复用支付单查单失败 payment=%s: %s", payment.payment_no, exc.message
                )
                synced, sync_meta = payment, {"synced": False, "trade_state": "QUERY_FAILED"}
            if synced.status == "PAID":
                progress = await cls.payment_progress(db, order)
                return synced, {
                    "status": "PAID",
                    "already_paid": True,
                    "synced": True,
                    **sync_meta,
                    **progress,
                }
            rebuilt = cls._rebuild_client_pay_params(payment)
            if rebuilt:
                progress = await cls.payment_progress(db, order)
                rebuilt.update(progress)
                rebuilt["reused"] = True
                return payment, rebuilt

        provider_inst = cls.get_provider(provider)
        try:
            pay_data = await provider_inst.create_payment(payment, order, extra)
        except AppException as exc:
            # 商户单号已存在：查单入账或关闭后换新单再下
            if reused and cls._is_duplicate_out_trade_error(exc):
                synced, sync_meta = await cls.sync_payment_status(db, payment)
                if synced.status == "PAID":
                    progress = await cls.payment_progress(db, order)
                    return synced, {
                        "status": "PAID",
                        "already_paid": True,
                        "synced": True,
                        **sync_meta,
                        **progress,
                    }
                await cls._close_pending_payment(db, payment, reason="渠道单号冲突，关闭后重建")
                t_no, t_ph = await _order_table_parts(db, order)
                sno = await _order_store_no(db, order)
                payment = Payment(
                    payment_no=generate_no(payment_biz(provider_u), sno, t_no, table_placeholder=t_ph),
                    order_id=order.id,
                    customer_id=customer_id or order.customer_id,
                    operator_id=operator_id,
                    provider=provider_u,
                    channel=channel,
                    amount=pay_amount,
                    status="PENDING",
                )
                db.add(payment)
                await db.flush()
                await record_payment_status(
                    db, payment_id=payment.id, from_status=None, to_status="PENDING", source="CREATE"
                )
                pay_data = await provider_inst.create_payment(payment, order, extra)
            else:
                raise
        cls._stash_client_pay_ref(payment, pay_data)
        await db.flush()
        if pay_data.get("_mock"):
            await cls._settle_mock_payment(db, payment, order, provider_u)
            pay_data["status"] = "PAID"
            pay_data["mock_settled"] = True
        elif pay_data.get("immediate_paid"):
            trade_no = pay_data.get("provider_trade_no") or pay_data.get("transaction_id") or pay_data.get("trade_no")
            # 金额必须与本地支付单一致，禁止仅因 SUCCESS 入账
            raw_amt = pay_data.get("provider_amount")
            if raw_amt is not None:
                try:
                    pa = money(Decimal(str(raw_amt)))
                    if pa != money(payment.amount):
                        payment.status = "UNKNOWN"
                        await db.flush()
                        raise AppException(
                            50041,
                            f"支付金额不一致：渠道 {pa} ≠ 本地 {money(payment.amount)}",
                        )
                except AppException:
                    raise
                except Exception:
                    payment.status = "UNKNOWN"
                    await db.flush()
                    raise AppException(50041, "支付金额校验失败")
            await cls._settle_immediate_payment(db, payment, order, provider_u, str(trade_no or ""))
            pay_data["status"] = "PAID"
        else:
            # 渠道已受理、待回调：保持 PENDING，等待回调/查单推进
            if payment.status not in ("PENDING", "REQUESTING"):
                payment.status = "PENDING"
                await db.flush()
        progress = await cls.payment_progress(db, order)
        pay_data.update(progress)
        return payment, pay_data

    @classmethod
    async def create_recharge_payment(
        cls,
        db: AsyncSession,
        *,
        recharge: RechargeOrder,
        provider: str,
        channel_type: str = "miniprogram",
        customer_id: int | None = None,
        extra: dict | None = None,
    ) -> tuple[Payment, dict]:
        """储值充值：微信/支付宝实付入账后写入本金+赠送金。"""
        from types import SimpleNamespace

        locked = (
            await db.execute(select(RechargeOrder).where(RechargeOrder.id == recharge.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40491, "充值单不存在")
        recharge = locked
        if recharge.status == "PAID":
            raise AppException(40010, "充值单已支付")
        if recharge.status not in ("PENDING",):
            raise AppException(40012, f"当前充值单状态不可支付: {recharge.status}")

        provider_u = provider.upper()
        if provider_u not in ("WECHAT", "ALIPAY"):
            raise AppException(40041, "充值仅支持微信或支付宝")
        channel = cls.CHANNEL_MAP.get(
            (provider_u, channel_type.lower()),
            f"{provider_u}_{channel_type.upper()}",
        )
        pay_amount = money(recharge.pay_amount)

        pending = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.recharge_order_id == recharge.id,
                    Payment.status == "PENDING",
                )
                .order_by(Payment.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        reused = False
        if pending and pending.provider == provider_u and pending.channel == channel:
            payment = pending
            reused = True
            if customer_id is not None:
                payment.customer_id = customer_id
        else:
            if pending:
                raise AppException(
                    40908,
                    "该充值单已有支付流程进行中，请先查单确认结果",
                    status_code=409,
                )
            store = await db.get(Store, recharge.store_id)
            sno = store.store_no if store else None
            payment = Payment(
                payment_no=generate_no(payment_biz(provider_u), sno, "00"),
                order_id=None,
                recharge_order_id=recharge.id,
                customer_id=customer_id or recharge.customer_id,
                provider=provider_u,
                channel=channel,
                amount=pay_amount,
                status="PENDING",
            )
            db.add(payment)
            await db.flush()
            await record_payment_status(
                db, payment_id=payment.id, from_status=None, to_status="PENDING", source="RECHARGE_CREATE"
            )
            recharge.payment_id = payment.id
            await db.flush()

        payment_id = payment.id
        recharge_id = recharge.id
        await db.commit()
        payment = await db.get(Payment, payment_id)
        recharge = await db.get(RechargeOrder, recharge_id)
        if not payment or not recharge:
            raise AppException(40491, "充值/支付单不存在")

        proxy_order = SimpleNamespace(
            order_no=recharge.recharge_no,
            channel="RECHARGE",
            dining_type=None,
            table_id=None,
            store_id=recharge.store_id,
        )

        if reused:
            try:
                synced, sync_meta = await cls.sync_payment_status(db, payment)
            except AppException as exc:
                logger.warning("复用充值支付单查单失败 payment=%s: %s", payment.payment_no, exc.message)
                synced, sync_meta = payment, {"synced": False, "trade_state": "QUERY_FAILED"}
            if synced.status == "PAID":
                return synced, {
                    "status": "PAID",
                    "already_paid": True,
                    "synced": True,
                    **sync_meta,
                }
            rebuilt = cls._rebuild_client_pay_params(payment)
            if rebuilt:
                rebuilt["reused"] = True
                return payment, rebuilt

        provider_inst = cls.get_provider(provider_u)
        try:
            pay_data = await provider_inst.create_payment(payment, proxy_order, extra)
        except AppException:
            raise
        cls._stash_client_pay_ref(payment, pay_data)
        await db.flush()
        if pay_data.get("_mock"):
            await cls._settle_recharge_payment(
                db, payment=payment, recharge=recharge, provider=provider_u, source="MOCK_RECHARGE"
            )
            pay_data["status"] = "PAID"
            pay_data["mock_settled"] = True
        elif pay_data.get("immediate_paid"):
            trade_no = pay_data.get("provider_trade_no") or pay_data.get("transaction_id") or pay_data.get("trade_no")
            payment.provider_trade_no = str(trade_no or "")
            await cls._settle_recharge_payment(
                db, payment=payment, recharge=recharge, provider=provider_u, source="RECHARGE_IMMEDIATE"
            )
            pay_data["status"] = "PAID"
        return payment, pay_data

    @classmethod
    async def _settle_recharge_payment(
        cls,
        db: AsyncSession,
        *,
        payment: Payment,
        recharge: RechargeOrder,
        provider: str,
        source: str,
        provider_trade_no: str | None = None,
    ) -> dict:
        """充值支付成功：记 SALE（实付）、入账本金/赠送金、充值单 PAID。"""
        if payment.status == "PAID" and recharge.status == "PAID":
            return {"already_paid": True, "status": "PAID"}

        from app.services.balance_service import BalanceService
        from app.services.financial_ledger_service import FinancialLedgerService

        # 统一充值单锁序：RechargeOrder → Payment，与 _lock_order_then_payment 一致。
        # 回调入口可能已持有充值单锁；反向先锁 Payment 会与并发结算形成死锁环。
        locked_r = (
            await db.execute(select(RechargeOrder).where(RechargeOrder.id == recharge.id).with_for_update())
        ).scalar_one()
        locked_p = (
            await db.execute(select(Payment).where(Payment.id == payment.id).with_for_update())
        ).scalar_one()
        recharge, payment = locked_r, locked_p
        if payment.status == "PAID" and recharge.status == "PAID":
            return {"already_paid": True, "status": "PAID"}

        old_p = payment.status
        payment.status = "PAID"
        if provider_trade_no:
            payment.provider_trade_no = provider_trade_no
        elif not payment.provider_trade_no:
            payment.provider_trade_no = f"rc_{payment.payment_no}"
        payment.paid_at = now_local()
        await record_payment_status(
            db, payment_id=payment.id, from_status=old_p, to_status="PAID", source=source
        )

        try:
            await FinancialLedgerService.record_sale(
                db,
                store_id=int(recharge.store_id),
                amount=payment.amount,
                channel="RECHARGE",
                provider=provider or payment.provider,
                order_id=None,
                payment_id=payment.id,
                remark=f"recharge_no={recharge.recharge_no}",
            )
        except Exception as exc:
            logger.exception("充值财务 SALE 失败 payment_id=%s", payment.id)
            from app.core.config import get_settings

            if get_settings().is_production:
                raise AppException(50010, "财务分录写入失败，充值未确认，请重试") from exc

        await BalanceService.credit_topup(db, recharge=recharge, payment_id=payment.id)
        recharge.status = "PAID"
        recharge.payment_id = payment.id
        recharge.paid_at = now_local()
        recharge.channel = (provider or payment.provider or "").upper()
        await db.flush()
        return {"status": "PAID", "recharge_no": recharge.recharge_no}

    @classmethod
    async def sync_recharge_payment(cls, db: AsyncSession, recharge: RechargeOrder) -> dict:
        """充值单主动查单：第三方网络请求前不持有充值单行锁。"""
        current = (
            await db.execute(
                select(RechargeOrder)
                .where(RechargeOrder.id == recharge.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not current:
            raise AppException(40491, "充值单不存在")
        if current.status == "PAID":
            return {"synced": False, "already_paid": True, "status": "PAID"}
        payment = None
        if current.payment_id:
            payment = await db.get(Payment, current.payment_id)
        if not payment:
            payment = (
                await db.execute(
                    select(Payment)
                    .where(Payment.recharge_order_id == current.id)
                    .order_by(Payment.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not payment:
            return {"synced": False, "trade_state": "NO_PAYMENT"}
        # sync_payment_status 先完成渠道 HTTP 查询，再按充值单→支付单锁序入账。
        payment, meta = await cls.sync_payment_status(db, payment)
        await db.refresh(current)
        return {**meta, "payment_no": payment.payment_no, "recharge_status": current.status}

    @classmethod
    def _is_duplicate_out_trade_error(cls, exc: AppException) -> bool:
        """仅匹配明确的商户订单号冲突错误码，避免把限频等临时错误误判为单号冲突。"""
        msg = str(getattr(exc, "message", "") or exc or "")
        needles = ("商户订单号重复", "out_trade_no", "OUT_TRADE_NO_USED", "ORDERPAID", "ACQ.TRADE_HAS_SUCCESS")
        return any(n.lower() in msg.lower() for n in needles)

    @classmethod
    def _stash_client_pay_ref(cls, payment: Payment, pay_data: dict) -> None:
        """保存拉起收银台所需凭证，便于复用未支付单时重签。"""
        if not pay_data or pay_data.get("_mock"):
            return
        ref = pay_data.get("prepay_id") or pay_data.get("trade_no") or pay_data.get("tradeNO")
        if ref:
            payment.provider_order_no = str(ref)[:64]

    @classmethod
    def _rebuild_client_pay_params(cls, payment: Payment) -> dict | None:
        ref = (payment.provider_order_no or "").strip()
        if not ref:
            return None
        channel = (payment.channel or "").upper()
        if payment.provider == "WECHAT" and ("MINIPROGRAM" in channel or "JSAPI" in channel):
            try:
                data = wechat_pay_client.build_jsapi_pay_params(
                    payment_no=payment.payment_no, prepay_id=ref
                )
                data["amount"] = str(payment.amount)
                data["reused"] = True
                return data
            except AppException:
                return None
        if payment.provider == "ALIPAY" and ("MINI" in channel or "JSAPI" in channel):
            return {
                "provider": "ALIPAY",
                "payment_no": payment.payment_no,
                "trade_no": ref,
                "amount": str(payment.amount),
                "reused": True,
            }
        return None

    @classmethod
    async def _close_pending_payment(cls, db: AsyncSession, payment: Payment, *, reason: str) -> None:
        if payment.status != "PENDING":
            return
        old = payment.status
        payment.status = "CLOSED"
        await record_payment_status(
            db,
            payment_id=payment.id,
            from_status=old,
            to_status="CLOSED",
            source="CREATE",
            remark=reason[:200] if reason else None,
        )
        await db.flush()

    @classmethod
    async def _lock_order_then_payment(
        cls, db: AsyncSession, payment_id: int
    ) -> Payment | None:
        """统一锁序：先 Order（若有）再 Payment，降低与关单路径的死锁概率。"""
        peek = (
            await db.execute(
                select(Payment.order_id, Payment.recharge_order_id).where(Payment.id == int(payment_id))
            )
        ).first()
        if peek is None:
            return None
        order_id, recharge_id = peek[0], peek[1]
        if order_id:
            await db.execute(select(Order).where(Order.id == int(order_id)).with_for_update())
        elif recharge_id:
            await db.execute(
                select(RechargeOrder).where(RechargeOrder.id == int(recharge_id)).with_for_update()
            )
        return (
            await db.execute(select(Payment).where(Payment.id == int(payment_id)).with_for_update())
        ).scalar_one_or_none()

    @classmethod
    async def _lock_order_then_payment_for_refund(
        cls, db: AsyncSession, refund: PaymentRefund
    ) -> tuple[Payment, Order]:
        """退款成功入账统一按 Order → Payment 加锁，与支付回调/订单状态机保持同序。

        退款行可能已由回调/补偿任务先锁定；但不得先锁 Payment 再锁 Order，
        否则会与 _lock_order_then_payment() 形成 Order↔Payment 死锁环。
        """
        order_id = int(refund.order_id) if refund.order_id is not None else 0
        if order_id <= 0:
            raise AppException(40302, "退款记录缺少有效订单关联")
        order = (
            await db.execute(
                select(Order).where(Order.id == order_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not order:
            raise AppException(40402, "退款关联订单不存在")
        payment = (
            await db.execute(
                select(Payment).where(Payment.id == int(refund.payment_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not payment:
            raise AppException(40401, "支付记录不存在")
        if payment.order_id is None or int(payment.order_id) != order_id:
            raise AppException(40302, "退款记录与原支付订单关联不一致")
        return payment, order

    @classmethod
    async def sync_payment_status(
        cls, db: AsyncSession, payment: Payment
    ) -> tuple[Payment, dict]:
        """主动向微信/支付宝查单并幂等入账（补偿回调丢失、支付结果不明）。

        CLOSED 仍查渠道：关单失败盲置 CLOSED 后，迟到成功可受控复活入账。
        查单在加行锁之前完成，避免把微信/支付宝 RTT 算进 Order/Payment 锁持有时间。
        （若调用方已在同一事务持有订单锁，仍须由调用方先释放；关单任务见 *_by_id。）
        """
        pid = int(payment.id)
        peek = (
            await db.execute(select(Payment).where(Payment.id == pid))
        ).scalar_one_or_none()
        if not peek:
            raise AppException(40401, "支付记录不存在")
        if peek.status == "PAID":
            return peek, {"synced": False, "trade_state": "SUCCESS", "already_paid": True}
        if peek.status in ("REFUNDED", "PARTIAL_REFUND"):
            return peek, {"synced": False, "trade_state": peek.status}

        # 渠道查单：不加 FOR UPDATE
        channel = await cls._query_channel_trade(peek)
        if channel.get("error"):
            return peek, {"synced": False, "trade_state": channel["error"]}
        if not channel.get("paid"):
            return peek, {
                "synced": False,
                "trade_state": channel.get("trade_state") or "UNKNOWN",
            }

        locked = await cls._lock_order_then_payment(db, pid)
        if not locked:
            raise AppException(40401, "支付记录不存在")
        payment = locked
        if payment.status == "PAID":
            return payment, {"synced": False, "trade_state": "SUCCESS", "already_paid": True}
        if payment.status in ("REFUNDED", "PARTIAL_REFUND"):
            return payment, {"synced": False, "trade_state": payment.status}
        if payment.status not in ("PENDING", "CLOSED"):
            return payment, {
                "synced": False,
                "trade_state": channel.get("trade_state") or payment.status,
            }
        await cls._settle_from_query(
            db,
            payment,
            provider=str(channel["provider"]),
            provider_trade_no=str(channel.get("provider_trade_no") or ""),
            query_raw=channel.get("query_raw"),
        )
        return payment, {
            "synced": True,
            "trade_state": channel.get("trade_state") or "SUCCESS",
            "status": "PAID",
        }

    @classmethod
    async def _query_channel_trade(cls, payment: Payment) -> dict:
        """向微信/支付宝查单（无 DB 锁）。供 sync 与 poll 锁外查询复用。"""
        provider_u = (payment.provider or "").upper()
        if provider_u in ("CASH", "BALANCE"):
            return {"error": payment.status, "paid": False}
        if provider_u == "WECHAT":
            if not (_wechat_pay_configured() or _wechat_micropay_configured()):
                return {"error": "NOT_CONFIGURED", "paid": False}
            channel = (payment.channel or "").upper()
            if "MICROPAY" in channel:
                from app.services import wechat_pay_v2_client

                q = await wechat_pay_v2_client.order_query(payment_no=payment.payment_no)
                trade_state = str(q.get("trade_state") or "").upper()
                trade_no = q.get("transaction_id")
                amount_ok = True
                if isinstance(q.get("raw"), dict) and q["raw"].get("total_fee"):
                    raw_amount = money(Decimal(str(q["raw"]["total_fee"])) / 100)
                    amount_ok = raw_amount == money(payment.amount)
                elif trade_state == "SUCCESS":
                    amount_ok = False
            else:
                if not _wechat_pay_configured():
                    return {"error": "NOT_CONFIGURED", "paid": False}
                q = await wechat_pay_client.query_by_out_trade_no(payment_no=payment.payment_no)
                trade_state = str(q.get("trade_state") or "").upper()
                trade_no = q.get("transaction_id")
                fen = q.get("amount_total_fen")
                if fen is None:
                    raise AppException(40302, "查单成功但缺少金额，拒绝入账")
                raw_amount = money(Decimal(str(fen)) / 100)
                amount_ok = raw_amount == money(payment.amount)
            if trade_state == "SUCCESS":
                if not amount_ok:
                    raise AppException(40302, "支付金额不匹配")
                return {
                    "paid": True,
                    "provider": "WECHAT",
                    "provider_trade_no": str(trade_no or ""),
                    "trade_state": "SUCCESS",
                    "query_raw": q.get("raw") if isinstance(q, dict) else q,
                }
            return {"paid": False, "trade_state": trade_state or "UNKNOWN"}

        if provider_u == "ALIPAY":
            if not _alipay_pay_configured():
                return {"error": "NOT_CONFIGURED", "paid": False}
            q = await alipay_client.trade_query(payment_no=payment.payment_no)
            trade_status = str(q.get("trade_status") or "").upper()
            if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
                total = q.get("total_amount")
                if total is None:
                    raise AppException(40302, "查单成功但缺少金额，拒绝入账")
                if money(Decimal(str(total))) != money(payment.amount):
                    raise AppException(40302, "支付金额不匹配")
                return {
                    "paid": True,
                    "provider": "ALIPAY",
                    "provider_trade_no": str(q.get("trade_no") or ""),
                    "trade_state": trade_status,
                    "query_raw": q.get("raw") if isinstance(q, dict) else q,
                }
            return {"paid": False, "trade_state": trade_status or "UNKNOWN"}

        return {"error": "UNSUPPORTED", "paid": False}

    @classmethod
    async def _settle_from_query(
        cls,
        db: AsyncSession,
        payment: Payment,
        *,
        provider: str,
        provider_trade_no: str,
        query_raw: dict | None,
    ) -> None:
        if payment.status == "PAID":
            return
        # 储值充值单：无餐饮 order_id
        if getattr(payment, "recharge_order_id", None) and not getattr(payment, "order_id", None):
            recharge = (
                await db.execute(
                    select(RechargeOrder).where(RechargeOrder.id == payment.recharge_order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if not recharge:
                raise AppException(40491, "充值单不存在")
            await cls._settle_recharge_payment(
                db,
                payment=payment,
                recharge=recharge,
                provider=provider,
                source="PAYMENT_QUERY",
                provider_trade_no=provider_trade_no,
            )
            payment.callback_raw = {"source": "PAYMENT_QUERY", "query": query_raw}
            await db.flush()
            return

        # 锁序已在 sync 入口持有 Order；此处再锁一次同事务无害
        order = (
            await db.execute(select(Order).where(Order.id == payment.order_id).with_for_update())
        ).scalar_one_or_none()
        if not order:
            raise AppException(40402, "订单不存在")
        # CANCELLED 允许受控复活（PAYMENT: CANCELLED→PAID）；其它终态拒绝
        if order.status == "REFUNDED" or order.payment_status == "REFUNDED":
            raise AppException(40901, "订单已退款，拒绝支付入账")
        if order.payment_status == "PAID" and payment.status in ("PENDING", "CLOSED"):
            old_p = payment.status
            if payment.status != "CLOSED":
                payment.status = "CLOSED"
                await record_payment_status(
                    db,
                    payment_id=payment.id,
                    from_status=old_p,
                    to_status="CLOSED",
                    source="QUERY",
                    remark="订单已支付，查单入账拒绝",
                )
            await db.flush()
            return

        old_p = payment.status
        payment.status = "PAID"
        payment.provider_trade_no = provider_trade_no or payment.provider_trade_no
        payment.paid_at = now_local()
        payment.callback_raw = {"source": "QUERY", "query": query_raw}
        await record_payment_status(
            db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="QUERY"
        )
        if old_p == "CLOSED":
            logger.warning(
                "CLOSED 支付查单复活入账 payment=%s order=%s",
                payment.payment_no,
                order.order_no,
            )
        await cls._finalize_tender(
            db, payment=payment, order=order, provider=provider, source="PAYMENT_QUERY"
        )
        await db.flush()
        logger.info(
            "查单入账成功 order=%s payment=%s provider=%s",
            order.order_no,
            payment.payment_no,
            provider,
        )

    @classmethod
    async def sync_order_pending_payment(
        cls, db: AsyncSession, order: Order, *, provider: str | None = None
    ) -> tuple[Payment | None, dict]:
        """按订单同步最近一笔待支付单；渠道 HTTP 查单必须在获取行锁前完成。"""
        # 这里只做普通读取，不能先锁 Order 再调用 sync_payment_status：
        # 后者需要请求微信/支付宝，网络 RTT 期间持有订单锁会放大高峰期锁等待。
        current_order = (
            await db.execute(
                select(Order)
                .where(Order.id == order.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not current_order:
            raise AppException(40402, "订单不存在")
        if current_order.payment_status == "PAID":
            return None, {
                "synced": False,
                "already_paid": True,
                "payment_status": "PAID",
                "order_status": current_order.status,
            }
        q = (
            select(Payment)
            .where(Payment.order_id == current_order.id, Payment.status == "PENDING")
            .order_by(Payment.id.desc())
            .limit(1)
        )
        if provider:
            q = q.where(Payment.provider == provider.upper())
        payment = (await db.execute(q)).scalar_one_or_none()
        if not payment:
            progress = await cls.payment_progress(db, current_order)
            return None, {"synced": False, "trade_state": "NO_PENDING", **progress}

        # sync_payment_status 先在锁外查渠道，再按 Order→Payment 顺序短事务加锁入账。
        payment, meta = await cls.sync_payment_status(db, payment)
        # 同步后重新读取订单，避免复用本事务中查单前的旧 ORM 快照。
        current_order = (
            await db.execute(
                select(Order)
                .where(Order.id == current_order.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not current_order:
            raise AppException(40402, "订单不存在")
        progress = await cls.payment_progress(db, current_order)
        return payment, {
            "payment_no": payment.payment_no,
            "status": payment.status,
            **meta,
            **progress,
        }

    @classmethod
    async def _settle_mock_payment(
        cls,
        db: AsyncSession,
        payment: Payment,
        order: Order,
        provider: str,
    ) -> None:
        """开发 mock 支付：跳过真实收银台，直接标记支付单已支付。"""
        if payment.status == "PAID":
            return
        old_p = payment.status
        payment.status = "PAID"
        payment.provider_trade_no = f"mock_{payment.payment_no}"
        payment.paid_at = now_local()
        payment.callback_raw = {"source": "MOCK", "settled": True}
        await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="MOCK")
        await cls._finalize_tender(db, payment=payment, order=order, provider=provider, source="MOCK_PAYMENT")
        logger.warning("MOCK 支付已自动入账 order=%s payment=%s", order.order_no, payment.payment_no)

    @classmethod
    async def _settle_immediate_payment(
        cls,
        db: AsyncSession,
        payment: Payment,
        order: Order,
        provider: str,
        provider_trade_no: str,
    ) -> None:
        if payment.status == "PAID":
            return
        old_p = payment.status
        payment.status = "PAID"
        payment.provider_trade_no = provider_trade_no or f"{provider.lower()}_{payment.payment_no}"
        payment.paid_at = now_local()
        payment.callback_raw = {"source": "BARCODE_PAY", "provider": provider}
        await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="BARCODE_PAY")
        await cls._finalize_tender(db, payment=payment, order=order, provider=provider, source="BARCODE_PAY")

    @classmethod
    async def settle_cash_payment(
        cls,
        db: AsyncSession,
        order: Order,
        *,
        operator_id: int | None = None,
        amount: Decimal | None = None,
        received_amount: Decimal | None = None,
        change_amount: Decimal | None = None,
        round_down: bool = False,
    ) -> tuple[Payment, dict]:
        """收银台现金入账（支持分笔；生产可用，不依赖第三方支付配置）。"""
        locked = (
            await db.execute(select(Order).where(Order.id == order.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40402, "订单不存在")
        order = locked

        if order.payment_status == "PAID" or order.status in (
            "PAID",
            "CONFIRMED",
            "PREPARING",
            "COOKING",
            "READY",
            "COMPLETED",
        ):
            progress = await cls.payment_progress(db, order)
            existing = (
                await db.execute(
                    select(Payment)
                    .where(Payment.order_id == order.id, Payment.status == "PAID")
                    .order_by(Payment.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing and progress.get("fully_paid"):
                return existing, {"status": "PAID", "already_paid": True, **progress}
        if order.status == "CANCELLED" or order.payment_status == "REFUNDED":
            raise AppException(40009, "订单已取消或已退款，无法收款")
        # P0 安全收敛：允许未支付 CONFIRMED 现金收款，作为异常恢复路径。
        if order.status not in ("PENDING_PAYMENT", "CONFIRMED"):
            raise AppException(40012, f"当前订单状态不可收款: {order.status}")

        remaining = await cls.remaining_amount(db, order)
        if remaining <= 0:
            raise AppException(40010, "订单已支付")

        pay_amount = money(amount) if amount is not None else remaining
        if pay_amount <= 0:
            raise AppException(40051, "收款金额非法")
        if pay_amount > remaining:
            raise AppException(40052, f"本笔金额超过待收余额 ¥{remaining}")

        # 抹零：向下取整到元（仅全额收款时生效）
        round_down_amount = Decimal("0.00")
        if round_down and pay_amount == remaining:
            from decimal import ROUND_FLOOR
            rounded = pay_amount.quantize(Decimal("1"), rounding=ROUND_FLOOR)
            round_down_amount = pay_amount - rounded
            pay_amount = rounded
            if pay_amount <= 0:
                raise AppException(40051, "抹零后金额非法")

        # 实收校验：至少覆盖本笔入账；找零由服务端计算且不得为负
        if received_amount is not None and money(received_amount) < pay_amount:
            raise AppException(40009, f"实收金额不足，本笔应收 ¥{pay_amount}")
        if change_amount is None and received_amount is not None:
            change_amount = money(received_amount) - pay_amount
        if change_amount is not None and money(change_amount) < 0:
            raise AppException(40009, "找零金额非法")
        if received_amount is not None and change_amount is not None:
            if money(received_amount) - money(change_amount) != pay_amount:
                # 以实收为准重算找零，避免客户端篡改
                change_amount = money(received_amount) - pay_amount
                if change_amount < 0:
                    raise AppException(40009, f"实收金额不足，本笔应收 ¥{pay_amount}")

        sno = await _order_store_no(db, order)
        t_no, t_ph = await _order_table_parts(db, order)
        payment = Payment(
            payment_no=generate_no(payment_biz("CASH"), sno, t_no, table_placeholder=t_ph),
            order_id=order.id,
            amount=pay_amount,
            provider="CASH",
            channel="POS",
            operator_id=operator_id,
            status="PENDING",
        )
        db.add(payment)
        await db.flush()
        await record_payment_status(db, payment_id=payment.id, from_status=None, to_status="PENDING", source="POS_CASH")
        old_p = payment.status
        payment.status = "PAID"
        payment.provider_trade_no = f"cash_{payment.payment_no}"
        payment.paid_at = now_local()
        payment.callback_raw = {
            "source": "POS_CASH",
            "operator_id": operator_id,
            **({"received_amount": str(received_amount)} if received_amount is not None else {}),
            **({"change_amount": str(change_amount)} if change_amount is not None else {}),
            **({"round_down_amount": str(round_down_amount)} if round_down_amount > 0 else {}),
        }
        await record_payment_status(db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="POS_CASH")
        tender = await cls._finalize_tender(
            db, payment=payment, order=order, provider="CASH", source="POS_CASH"
        )
        progress = await cls.payment_progress(db, order)
        logger.info(
            "收银现金入账 order=%s payment=%s amount=%s operator=%s fully=%s",
            order.order_no,
            payment.payment_no,
            pay_amount,
            operator_id,
            tender.get("fully_paid"),
        )
        return payment, {
            "status": "PAID",
            "provider": "CASH",
            "change_amount": str(change_amount) if change_amount is not None else None,
            **progress,
        }

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
            # P0-10: 微信回调身份 fail-closed — mchid 必须匹配
            # P2 修复：生产环境 mchid 缺失也拒绝（符合硬约束"must reject if missing or mismatched"）
            callback_mchid = str(parsed_probe.get("mchid") or "").strip()
            expected_mchid = str(get_settings().wechat_mch_id or "").strip()
            if get_settings().is_production:
                if not callback_mchid:
                    logger.warning("微信回调缺少 mchid，生产环境拒绝处理")
                    raise AppException(40054, "微信回调缺少商户号，拒绝处理")
                if expected_mchid and callback_mchid != expected_mchid:
                    logger.warning("微信回调 mchid 不匹配: got=%s expected=%s", callback_mchid, expected_mchid)
                    raise AppException(40054, "微信回调商户号不匹配，拒绝处理")
            elif expected_mchid and callback_mchid and callback_mchid != expected_mchid:
                logger.warning("微信回调 mchid 不匹配: got=%s expected=%s", callback_mchid, expected_mchid)
                raise AppException(40054, "微信回调商户号不匹配，拒绝处理")
            if parsed_probe.get("is_refund"):
                receipt = make_receipt_key(
                    f"{provider_u}:REFUND",
                    *_wechat_callback_receipt_identity(data, parsed_probe),
                )
            else:
                receipt = make_receipt_key(
                    provider_u,
                    *_wechat_callback_receipt_identity(data, parsed_probe),
                )
        else:
            data = AlipayVerifier().verify_and_parse(headers, body)
            parsed_probe = await provider_inst.handle_callback(data)
            # P0-10: 支付宝回调身份 fail-closed — app_id 必须匹配
            # P2 修复：生产环境 app_id 缺失也拒绝
            callback_appid = str(parsed_probe.get("app_id") or "").strip()
            expected_appid = str(get_settings().alipay_app_id or "").strip()
            if get_settings().is_production:
                if not callback_appid:
                    logger.warning("支付宝回调缺少 app_id，生产环境拒绝处理")
                    raise AppException(40054, "支付宝回调缺少应用ID，拒绝处理")
                if expected_appid and callback_appid != expected_appid:
                    logger.warning("支付宝回调 app_id 不匹配: got=%s expected=%s", callback_appid, expected_appid)
                    raise AppException(40054, "支付宝回调应用ID不匹配，拒绝处理")
            elif expected_appid and callback_appid and callback_appid != expected_appid:
                logger.warning("支付宝回调 app_id 不匹配: got=%s expected=%s", callback_appid, expected_appid)
                raise AppException(40054, "支付宝回调应用ID不匹配，拒绝处理")
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

        redis_channel = f"pay:{provider_u}"
        claim_token = await claim_once(redis_channel, receipt)
        if not claim_token:
            # Redis 显示已认领：可能是真重复，也可能是崩溃残留。
            # 必须查 DB receipt 判断事实；若没有 DB receipt，继续依赖 DB UNIQUE 竞争，
            # 但绝不能删除未知请求的 Redis 锁（它可能仍由正在执行的请求持有）。
            receipt_provider_check = (
                f"{provider_u}:REFUND" if parsed_probe.get("is_refund") else provider_u
            )
            existing_receipt = (
                await db.execute(
                    select(PaymentCallbackReceipt).where(
                        PaymentCallbackReceipt.provider == receipt_provider_check,
                        PaymentCallbackReceipt.receipt_key == receipt,
                    )
                )
            ).scalar_one_or_none()
            if existing_receipt:
                # 真重复：receipt 已落库，业务已处理过，短路返回现有对象。
                if parsed_probe.get("is_refund"):
                    refund_no = parsed_probe.get("refund_no")
                    if refund_no:
                        return (
                            await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))
                        ).scalar_one_or_none()
                    return None
                result = await db.execute(select(Payment).where(Payment.payment_no == parsed_probe.get("payment_no")))
                return result.scalar_one_or_none()
            # 无 DB receipt 可能是崩溃残留，也可能是另一请求尚未提交。
            # 继续由数据库唯一约束仲裁；当前请求没有 Redis 锁所有权，不得释放它。

        receipt_provider = f"{provider_u}:REFUND" if parsed_probe.get("is_refund") else provider_u
        try:
            db.add(
                PaymentCallbackReceipt(
                    provider=receipt_provider,
                    receipt_key=receipt,
                    payment_no=data.get("out_trade_no"),
                )
            )
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # 不能仅凭 IntegrityError 就认定 receipt 重复；只有按 provider+receipt_key
            # 查到已存在的回执才算并发重复。FK/NOT NULL/其他唯一键错误必须向平台报错，
            # 否则平台收到成功 ACK、而本地没有处理支付的风险会变成永久漏单。
            existing_receipt = (
                await db.execute(
                    select(PaymentCallbackReceipt).where(
                        PaymentCallbackReceipt.provider == receipt_provider,
                        PaymentCallbackReceipt.receipt_key == receipt,
                    )
                )
            ).scalar_one_or_none()
            if existing_receipt is None:
                await release_claim(redis_channel, receipt, claim_token)
                raise
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
            if parsed_probe.get("is_refund"):
                return await cls._apply_refund_callback(db, provider_u, data, parsed_probe, receipt)
            return await cls._apply_payment_callback(db, provider_u, provider_inst, data, receipt)
        except Exception:
            # 回执与支付/退款业务写入属于同一 DB 事务；rollback 会原子撤销本请求的回执。
            # 绝不能用独立 Session 按 receipt_key 删除“查到的回执”：它可能属于并发请求
            # 已成功提交的事务，删除后会重新开放已处理回调，造成重复入账/退款。
            try:
                await db.rollback()
            except Exception:
                logger.exception("支付回调失败后 DB rollback 异常 receipt=%s", receipt)
            # 仅释放本请求拥有的 Redis claim；若本请求是 Redis 降级放行则 Lua 安全 no-op。
            await release_claim(redis_channel, receipt, claim_token)
            raise

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
        # 先取 id，再 Order→Payment 统一锁序
        peek = (
            await db.execute(
                select(Payment.id).where(Payment.payment_no == parsed["payment_no"])
            )
        ).scalar_one_or_none()
        if not peek:
            raise AppException(40401, "支付记录不存在")
        payment = await cls._lock_order_then_payment(db, int(peek))
        if not payment:
            raise AppException(40401, "支付记录不存在")
        if str(payment.provider or "").strip().upper() != str(provider_u or "").strip().upper():
            raise AppException(40302, "支付回调渠道与本地支付记录不匹配")

        if payment.status == "PAID":
            if str(parsed.get("status") or "").strip().upper() == "PAID":
                _validate_duplicate_paid_callback(provider_u, payment, parsed)
            return payment

        # 储值充值回调
        if payment.recharge_order_id and not payment.order_id:
            return await cls._apply_recharge_callback(
                db, provider_u=provider_u, payment=payment, parsed=parsed, receipt=receipt, data=data
            )

        order = (
            await db.execute(select(Order).where(Order.id == payment.order_id).with_for_update())
        ).scalar_one_or_none()
        if not order:
            raise AppException(40402, "订单不存在")
        if order.status == "REFUNDED" or order.payment_status == "REFUNDED":
            raise AppException(40901, "订单已退款，拒绝支付入账")
        # CANCELLED：允许迟到回调复活（与查单路径一致）
        if order.payment_status == "PAID" and order.status != "CANCELLED":
            logger.warning(
                "重复支付回调已拒绝 order=%s payment=%s",
                order.order_no,
                payment.payment_no,
            )
            if payment.status in ("PENDING", "CLOSED"):
                old_p = payment.status
                if payment.status != "CLOSED":
                    payment.status = "CLOSED"
                    payment.fail_reason = "订单已支付，重复入账拒绝"
                    await record_payment_status(
                        db,
                        payment_id=payment.id,
                        from_status=old_p,
                        to_status="CLOSED",
                        source="CALLBACK",
                    )
            await db.flush()
            return payment

        settings = get_settings()
        if provider_u == "WECHAT":
            mch = parsed.get("mchid") or data.get("mchid")
            # P2 修复：生产环境 mchid 缺失或不匹配一律拒绝
            if settings.is_production:
                if not mch:
                    raise AppException(40301, "支付回调缺少商户号")
                if str(mch) != str(settings.wechat_mch_id):
                    raise AppException(40301, "支付商户号不匹配")
            elif mch and str(mch) != str(settings.wechat_mch_id):
                raise AppException(40301, "支付商户号不匹配")
        else:
            app_id = parsed.get("app_id") or data.get("app_id")
            if settings.is_production:
                if not app_id:
                    raise AppException(40301, "支付回调缺少应用ID")
                if settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
                    raise AppException(40301, "支付宝 app_id 不匹配")
            elif app_id and settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
                raise AppException(40301, "支付宝 app_id 不匹配")

        # 币种：有则必须 CNY（微信 amount.currency / 支付宝 currency）
        currency = parsed.get("currency")
        if isinstance(parsed.get("amount"), dict) and not currency:
            currency = parsed["amount"].get("currency")
        if currency and str(currency).upper() != "CNY":
            raise AppException(40302, f"支付币种不匹配，仅支持 CNY，收到: {currency}")

        raw_amount = parsed.get("amount", 0)
        if isinstance(raw_amount, dict):
            raw_amount = raw_amount.get("total", 0)
        # fail-closed：回调金额缺失或格式非法时拒绝入账，防止 Decimal(str(None)) 崩溃
        if raw_amount is None:
            raise AppException(40302, "回调金额缺失或格式非法")
        try:
            if provider_u == "WECHAT":
                callback_amount = money(Decimal(str(raw_amount)) / 100)
            else:
                callback_amount = money(Decimal(str(raw_amount)))
        except (InvalidOperation, ValueError, TypeError):
            raise AppException(40302, "回调金额缺失或格式非法")
        if callback_amount != money(payment.amount):
            raise AppException(40302, "支付金额不匹配")

        if parsed["status"] == "PAID":
            old_p = payment.status
            payment.status = "PAID"
            payment.provider_trade_no = parsed.get("provider_trade_no")
            payment.paid_at = now_local()
            payment.callback_raw = {"receipt": receipt}
            await record_payment_status(
                db, payment_id=payment.id, from_status=old_p, to_status="PAID", source="CALLBACK"
            )
            # 与扫码/现金一致：分笔未结清仅 PARTIAL_PAID，结清才订单 PAID
            await cls._finalize_tender(
                db, payment=payment, order=order, provider=provider_u, source="CALLBACK"
            )
            payment._callback_transitioned_to_paid = True

        await db.flush()
        return payment

    @classmethod
    async def _apply_recharge_callback(
        cls,
        db: AsyncSession,
        *,
        provider_u: str,
        payment: Payment,
        parsed: dict,
        receipt: str,
        data: dict,
    ) -> Payment:
        settings = get_settings()
        if provider_u == "WECHAT":
            mch = parsed.get("mchid") or data.get("mchid")
            if settings.is_production:
                if not mch:
                    raise AppException(40301, "支付回调缺少商户号")
                if str(mch) != str(settings.wechat_mch_id):
                    raise AppException(40301, "支付商户号不匹配")
            elif mch and str(mch) != str(settings.wechat_mch_id):
                raise AppException(40301, "支付商户号不匹配")
        else:
            app_id = parsed.get("app_id") or data.get("app_id")
            if settings.is_production:
                if not app_id:
                    raise AppException(40301, "支付回调缺少应用ID")
                if settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
                    raise AppException(40301, "支付宝 app_id 不匹配")
            elif app_id and settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
                raise AppException(40301, "支付宝 app_id 不匹配")

        currency = parsed.get("currency")
        if isinstance(parsed.get("amount"), dict) and not currency:
            currency = parsed["amount"].get("currency")
        if currency and str(currency).upper() != "CNY":
            raise AppException(40302, f"支付币种不匹配，仅支持 CNY，收到: {currency}")

        raw_amount = parsed.get("amount", 0)
        if isinstance(raw_amount, dict):
            raw_amount = raw_amount.get("total", 0)
        # fail-closed：回调金额缺失或格式非法时拒绝入账，防止 Decimal(str(None)) 崩溃
        if raw_amount is None:
            raise AppException(40302, "回调金额缺失或格式非法")
        try:
            if provider_u == "WECHAT":
                callback_amount = money(Decimal(str(raw_amount)) / 100)
            else:
                callback_amount = money(Decimal(str(raw_amount)))
        except (InvalidOperation, ValueError, TypeError):
            raise AppException(40302, "回调金额缺失或格式非法")
        if callback_amount != money(payment.amount):
            raise AppException(40302, "支付金额不匹配")

        if parsed["status"] == "PAID":
            recharge = (
                await db.execute(
                    select(RechargeOrder)
                    .where(RechargeOrder.id == payment.recharge_order_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not recharge:
                raise AppException(40491, "充值单不存在")
            payment.callback_raw = {"receipt": receipt}
            await cls._settle_recharge_payment(
                db,
                payment=payment,
                recharge=recharge,
                provider=provider_u,
                source="CALLBACK",
                provider_trade_no=parsed.get("provider_trade_no"),
            )
            payment._callback_transitioned_to_paid = True
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
        refund_no = str(parsed.get("refund_no") or "").strip()
        # 退款通知必须携带商户退款请求号，并精确命中本地 refund_no。
        # 不能按 payment_no 猜测“最近一笔进行中退款”：迟到/重试的旧通知可能被错误绑定到新退款。
        if not refund_no:
            raise AppException(40302, "退款回调缺少商户退款单号，拒绝自动匹配")
        # 先无锁读取退款单。支付宝分支随后需要调用 refund.query；
        # 绝不能在第三方 HTTP 请求期间持有 PaymentRefund 行锁。
        row = (
            await db.execute(
                select(PaymentRefund).where(PaymentRefund.refund_no == refund_no)
            )
        ).scalar_one_or_none()

        if not row:
            raise AppException(40403, "退款记录不存在")

        # 不能只凭退款单号命中本地记录：还必须校验签名回调中的原支付单、
        # provider 和订单归属，避免跨支付单/跨渠道退款通知误更新本地退款状态。
        linked_payment = await db.get(Payment, row.payment_id)
        _validate_refund_callback_binding(provider_u, parsed, row, linked_payment)

        status_raw = str(parsed.get("refund_status") or "").strip().upper()
        provider_refund_no = parsed.get("provider_refund_no")

        if provider_u == "WECHAT":
            # 微信 amount.total 是原支付总金额，amount.refund 是本次退款金额，单位均为分。
            amount_info = parsed.get("amount")
            if not isinstance(amount_info, dict) or amount_info.get("total") is None:
                raise AppException(40302, "微信退款回调缺少原订单总金额")
            try:
                provider_total_amount = money(Decimal(str(amount_info["total"])) / 100)
            except (InvalidOperation, ValueError) as exc:
                raise AppException(40302, "微信退款原订单总金额解析失败") from exc
            if provider_total_amount != money(linked_payment.amount):
                raise AppException(40302, "微信退款回调原订单总金额与本地支付金额不匹配")
            callback_currency = str(amount_info.get("currency") or "CNY").upper()
            if callback_currency != "CNY":
                raise AppException(40302, "微信退款回调币种必须为 CNY")

            # amount.refund 是本次退款金额，必须与本地退款申请金额一致。
            refund_amount_raw = parsed.get("refund_amount")
            if refund_amount_raw is None:
                raise AppException(40302, "微信退款回调缺少本次退款金额")
            try:
                provider_refund_amount = money(Decimal(str(refund_amount_raw)) / 100)
            except (InvalidOperation, ValueError) as exc:
                raise AppException(40302, "微信退款金额解析失败") from exc
            if provider_refund_amount != money(row.amount):
                raise AppException(40302, "微信本次退款金额与本地退款申请不匹配")

            event_type = str(data.get("event_type") or "").strip().upper()
            if status_raw == "SUCCESS" or event_type == "REFUND.SUCCESS":
                new_status = "SUCCESS"
            elif status_raw == "CLOSED" or event_type == "REFUND.CLOSED":
                new_status = "CLOSED"
            elif status_raw == "ABNORMAL":
                new_status = "ABNORMAL"
            else:
                new_status = "PROCESSING"
        else:
            # 支付宝 refund_fee 是“该笔交易累计已成功退款总额”，不是本次退款金额。
            # 必须用 out_biz_no 定位单笔退款，再调用 refund.query 确认该请求的状态与金额。
            cumulative_raw = parsed.get("refund_fee")
            cumulative_amount = None
            if cumulative_raw is not None:
                try:
                    cumulative_amount = money(Decimal(str(cumulative_raw)))
                except (InvalidOperation, ValueError) as exc:
                    raise AppException(40302, "支付宝累计退款金额解析失败") from exc
                if cumulative_amount < 0 or cumulative_amount > money(linked_payment.amount):
                    raise AppException(40302, "支付宝累计退款金额超出原支付金额")

            # 回调中的 refund_fee 不能证明当前 out_request_no 已成功；
            # 通过官方 refund.query 按单笔商户退款请求号核验，解决多次部分退款累计金额歧义。
            query_result = await alipay_client.trade_refund_query(
                payment_no=str(linked_payment.payment_no),
                refund_no=str(row.refund_no),
            )
            query_status = _resolve_alipay_refund_status({"refund_status": str((query_result or {}).get("status") or "PROCESSING").upper()})
            query_amount_raw = (query_result or {}).get("amount")
            if query_status == "SUCCESS":
                if query_amount_raw is None:
                    raise AppException(50302, "支付宝退款查询未返回本次退款金额，暂不确认成功")
                try:
                    query_amount = money(Decimal(str(query_amount_raw)))
                except (InvalidOperation, ValueError) as exc:
                    raise AppException(50302, "支付宝退款查询金额解析失败") from exc
                if query_amount != money(row.amount):
                    raise AppException(40302, "支付宝退款查询的本次退款金额与本地申请不匹配")
                if cumulative_amount is not None and cumulative_amount < money(row.amount):
                    raise AppException(40302, "支付宝累计退款金额小于本次已成功退款金额")
                new_status = "SUCCESS"
            elif query_status in ("FAILED", "CLOSED"):
                new_status = query_status
            else:
                new_status = "PROCESSING"
            provider_refund_no = (query_result or {}).get("provider_refund_no") or provider_refund_no

        # 渠道查询已在无行锁状态下完成。现在重新获取当前退款行并加锁，
        # 防止查询期间其他回调/查单已完成状态迁移；populate_existing 确保刷新旧快照。
        # Acquire parent rows first, matching refund creation/finalization. This
        # must happen after any provider HTTP query and before PaymentRefund FOR UPDATE.
        locked_payment = await cls._lock_order_then_payment(db, int(row.payment_id))
        if locked_payment is None:
            raise AppException(40401, "支付记录不存在")
        row = (
            await db.execute(
                select(PaymentRefund)
                .where(PaymentRefund.id == int(row.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            raise AppException(40403, "退款记录不存在")
        linked_payment = await db.get(Payment, row.payment_id)
        _validate_refund_callback_binding(provider_u, parsed, row, linked_payment)

        return await cls._finalize_refund(
            db,
            row,
            new_status,
            provider_refund_no=provider_refund_no,
            callback_raw={"receipt": receipt, "parsed": {k: parsed.get(k) for k in (
                "refund_no", "refund_status", "payment_no", "provider_refund_no", "refund_fee", "fund_change", "gmt_refund", "event_type"
            )}},
            source="CALLBACK",
        )

    @classmethod
    async def _sync_pos_refund_request_on_success(
        cls, db: AsyncSession, refund: PaymentRefund
    ) -> None:
        """将异步渠道退款成功原子同步到对应 POS 退款申请，按 refund_id 严格关联。"""
        request = (
            await db.execute(
                select(PosRefundRequest)
                .where(PosRefundRequest.refund_id == int(refund.id))
                .with_for_update()
                .limit(1)
            )
        ).scalar_one_or_none()
        if request is None or request.status in ("SUCCESS", "REJECTED", "CANCELLED"):
            return

        executed = money(Decimal(str(request.executed_amount or 0)) + money(refund.amount))
        request.executed_amount = executed
        request.payment_id = int(refund.payment_id)
        request.fail_reason = None
        if executed > money(request.amount):
            # 记录实际已退金额，但明确暴露申请金额被超出的异常，绝不静默封账。
            request.status = "RECONCILIATION_REQUIRED"
            request.fail_reason = (
                f"实际退款累计 ¥{executed} 超过申请金额 ¥{money(request.amount)}，需人工核对"
            )[:255]
        elif executed >= money(request.amount):
            request.status = "SUCCESS"
        else:
            request.status = "PROCESSING"
        await db.flush()

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
        # P1-14 Refund state machine: PENDING -> PROCESSING -> {SUCCESS, FAILED, CLOSED, UNKNOWN, ABNORMAL}.
        # Idempotent: SUCCESS is terminal; re-finalizing a SUCCESS row is a no-op.
        # UNKNOWN = provider returned an ambiguous result; must NOT be promoted to SUCCESS;
        #   recovery happens via sync_refund_status query / scheduled compensation.
        if row.status == "SUCCESS":
            return row
        # 支付平台通知可能乱序：终态退款不能被迟到的 PROCESSING/UNKNOWN 覆盖。
        # FAILED/CLOSED 仅允许后续权威 SUCCESS 查询/通知纠正；ABNORMAL 必须等待人工/平台处理结果。
        if row.status in ("FAILED", "CLOSED") and new_status != "SUCCESS":
            return row
        if row.status == "ABNORMAL" and new_status in ("PENDING", "PROCESSING", "UNKNOWN"):
            return row

        if provider_refund_no:
            row.provider_refund_no = provider_refund_no
        if callback_raw is not None:
            row.callback_raw = callback_raw
        if fail_reason:
            row.fail_reason = fail_reason[:255]

        if new_status == "SUCCESS":
            row.status = "SUCCESS"
            row._callback_transitioned_to_success = True
            row.succeeded_at = now_local()
            # 先锁订单再锁支付单，避免与支付回调的 Order→Payment 锁序死锁。
            payment, order = await cls._lock_order_then_payment_for_refund(db, row)
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
            # POS 异步退款在审批返回后才收到 SUCCESS 回调；必须在同一事务内累计执行金额。
            await cls._sync_pos_refund_request_on_success(db, row)

            if order:
                provider_u = (payment.provider or "").upper()
                if provider_u != "BALANCE":
                    try:
                        from app.services.financial_ledger_service import FinancialLedgerService

                        await FinancialLedgerService.record_refund(
                            db,
                            store_id=int(order.store_id),
                            amount=row.amount,
                            channel=order.channel or payment.channel,
                            provider=payment.provider,
                            order_id=order.id,
                            payment_id=payment.id,
                            refund_id=row.id,
                            remark=f"refund_no={row.refund_no}",
                        )
                    except Exception as exc:
                        logger.exception(
                            "财务分录 REFUND 写入失败 refund_id=%s order_id=%s", row.id, order.id
                        )
                        from app.core.config import get_settings

                        if get_settings().is_production:
                            raise AppException(50010, "财务分录写入失败，退款未确认，请重试") from exc
                else:
                    # 余额支付退款：回补钱包，不记财务 REFUND（原支付未记 SALE）
                    if order.customer_id:
                        from app.services.balance_service import BalanceService

                        await BalanceService.restore_for_refund(
                            db,
                            customer_id=int(order.customer_id),
                            order_id=order.id,
                            refund_id=row.id,
                            amount=row.amount,
                            store_id=order.store_id,
                        )
                # 按订单净已收判断：分笔退满一笔不得误标整单 REFUNDED
                net = await cls.order_net_collected(db, order.id)
                total = money(order.total_amount)
                if net <= 0:
                    # 公益台账与营销归因冲减必须和退款 SUCCESS 同事务写入 Outbox。
                    # 不在资金确认事务中吞掉副作用异常；worker 使用 refund_id 幂等重试。
                    from app.services.outbox_worker import enqueue_outbox

                    for event_kind in ("social_benefit_reverse", "campaign_attribution_reverse"):
                        await enqueue_outbox(
                            db,
                            aggregate_type="refund",
                            aggregate_id=int(row.id),
                            event_kind=event_kind,
                            payload={"refund_id": int(row.id), "order_id": int(order.id)},
                        )
                    if order.status not in ("CANCELLED", "REFUNDED"):
                        await apply_order_status(
                            db,
                            order,
                            "REFUNDED",
                            source="PAYMENT",
                            payment_status="REFUNDED",
                            assert_kind="SYSTEM",
                            remark=f"refund_no={row.refund_no}",
                        )
                    else:
                        order.payment_status = "REFUNDED"
                        # 订单已是终态时 apply_order_status 不会再走 release；幂等补一次
                        from app.services.coupon_service import CouponService
                        from app.services.paid_side_effects import enqueue_coupon_release

                        try:
                            await CouponService.release_for_order(db, order)
                        except Exception:
                            logger.exception(
                                "终态订单补回滚优惠券失败，已入队补偿 order_id=%s refund_no=%s",
                                order.id,
                                row.refund_no,
                            )
                            await enqueue_coupon_release(db, int(order.id))
                    # 全额退：回收因该单计入而超出阈值的未用里程碑券
                    if order.customer_id:
                        from app.services.coupon_service import CouponService
                        from app.services.outbox_worker import enqueue_outbox

                        try:
                            await CouponService.reclaim_milestones_after_full_refund(
                                db, order, refund_id=int(row.id)
                            )
                        except Exception:
                            logger.exception(
                                "里程碑券回收失败，已入队 outbox order_id=%s refund_no=%s",
                                order.id,
                                row.refund_no,
                            )
                            try:
                                await enqueue_outbox(
                                    db,
                                    aggregate_type="refund",
                                    aggregate_id=int(row.id),
                                    event_kind="milestone_reclaim",
                                    payload={
                                        "order_id": int(order.id),
                                        "refund_id": int(row.id),
                                    },
                                )
                            except Exception:
                                logger.exception(
                                    "入队 milestone_reclaim 失败 refund_id=%s", row.id
                                )
                elif net < total:
                    order.payment_status = "PARTIAL_REFUND"
                else:
                    order.payment_status = "PAID"
                if order.customer_id:
                    # 退款已确认 SUCCESS（provider 资金已退）；积分扣减/口味重算失败不再静默吞，
                    # 入队 DB outbox 由 worker 重驱，避免「钱退了积分没扣」（#3 分布式事务补偿）。
                    # 各 handler 按 refund_id 幂等。
                    from app.services.outbox_worker import enqueue_outbox

                    try:
                        from app.services.customer_service import CustomerService, PreferenceService

                        await CustomerService.deduct_points_for_refund(
                            db, order, row.amount, refund_id=row.id
                        )
                        # 退款后按剩余已付订单重算口味，避免已退订单继续撑着旧偏好
                        await PreferenceService.recompute_for_customer(db, order.customer_id)
                    except Exception:
                        logger.exception(
                            "退款扣减积分或口味重算失败，已入队 outbox 补偿 order_id=%s refund_no=%s",
                            order.id,
                            row.refund_no,
                        )
                        # 补偿事件：aggregate_id=refund_id 保证每笔退款各一条，幂等
                        for kind, payload in (
                            ("points_deduct_refund", {"order_id": int(order.id), "refund_id": int(row.id)}),
                            ("pref_recompute", {"order_id": int(order.id), "refund_id": int(row.id)}),
                        ):
                            try:
                                await enqueue_outbox(
                                    db,
                                    aggregate_type="refund",
                                    aggregate_id=int(row.id),
                                    event_kind=kind,
                                    payload=payload,
                                )
                            except Exception:
                                logger.exception(
                                    "入队退款补偿 outbox 失败 refund_id=%s kind=%s", row.id, kind
                                )
        elif new_status in ("CLOSED", "ABNORMAL", "FAILED"):
            row.status = new_status
            row.fail_reason = row.fail_reason or fail_reason or (
                "微信退款异常：需要在微信商户平台处理异常退款后再查询确认" if new_status == "ABNORMAL" else new_status
            )
        elif new_status == "UNKNOWN":
            # 保留 UNKNOWN，不得升格为 SUCCESS；由 query / 定时补偿恢复
            row.status = "UNKNOWN"
        else:
            # PROCESSING / PENDING 等中间态：明确禁止当 SUCCESS
            row.status = "PROCESSING"

        await db.flush()
        return row

    @classmethod
    async def refundable_amount(cls, db: AsyncSession, payment: Payment) -> Decimal:
        """可退余额 = 支付金额 − (成功+进行中+未知+异常待处理)退款。"""
        used = await db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("SUCCESS", "PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
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
        pos_refund_request_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> PaymentRefund:
        # Keep refund-row locks behind the shared Order/RechargeOrder → Payment
        # lock order. Callback and compensation paths use the same order before
        # locking PaymentRefund, preventing a refund-row/payment-row deadlock.
        locked = await cls._lock_order_then_payment(db, int(payment.id))
        if not locked:
            raise AppException(40401, "支付记录不存在")
        payment = locked
        # HTTP 客户端可用 Idempotency-Key 安全重试退款请求。
        # 必须在支付终态检查之前查询，才能在“部分退款成功但响应丢失”后返回原退款单，
        # 而不是再次向渠道发起一笔新的部分退款。
        idem_key = str(idempotency_key or "").strip() or None
        if idem_key is not None:
            if len(idem_key) > 64:
                raise AppException(40055, "退款幂等键长度不能超过 64 个字符")
            existing_refund = (
                await db.execute(
                    select(PaymentRefund)
                    .where(PaymentRefund.idempotency_key == idem_key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing_refund is not None:
                if (
                    int(existing_refund.payment_id) != int(payment.id)
                    or money(existing_refund.amount) != money(amount)
                    or str(existing_refund.reason or "") != str(reason or "")
                ):
                    raise AppException(40955, "退款幂等键已被不同的支付单或退款参数使用")
                # 标记这是客户端重试命中旧记录；API 层据此避免重复审计/广播副作用。
                existing_refund._idempotent_replay = True
                return existing_refund
        if payment.status == "REFUNDED":
            raise AppException(40053, "该支付已全额退款，不可重复退款")
        if payment.status not in ("PAID", "PARTIAL_REFUND"):
            raise AppException(40050, "仅已支付可退款")
        amount = money(amount)
        if amount <= 0:
            raise AppException(40051, "退款金额非法")

        # 进行中/未知/异常待处理的退款单：禁止再开一笔，避免重复退款
        inflight = await db.scalar(
            select(func.count()).select_from(PaymentRefund).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
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

        order = await db.get(Order, payment.order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        t_no, t_ph = await _order_table_parts(db, order)
        sno = await _order_store_no(db, order)
        refund_no = generate_no(refund_biz(payment.provider), sno, t_no, table_placeholder=t_ph)
        locked_payment_id = int(payment.id)
        row = PaymentRefund(
            refund_no=refund_no,
            payment_id=locked_payment_id,
            order_id=payment.order_id,
            amount=amount,
            reason=reason,
            status="PENDING",
            operator_id=operator_id,
            idempotency_key=idem_key,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # 不同支付单并发复用同一幂等键时，先由唯一索引裁决；
            # 回滚失败事务后重新读取胜出的退款单，匹配参数则安全返回原单。
            await db.rollback()
            if idem_key is not None:
                winner = (
                    await db.execute(
                        select(PaymentRefund).where(
                            PaymentRefund.idempotency_key == idem_key
                        )
                    )
                ).scalar_one_or_none()
                if winner is not None:
                    if (
                        int(winner.payment_id) != locked_payment_id
                        or money(winner.amount) != money(amount)
                        or str(winner.reason or "") != str(reason or "")
                    ):
                        raise AppException(
                            40955, "退款幂等键已被不同的支付单或退款参数使用"
                        )
                    winner._idempotent_replay = True
                    return winner
            # 不是幂等键冲突（例如退款单号冲突）时保留原始数据库异常，
            # 不伪装成可重试的幂等成功。
            raise

        if pos_refund_request_id is not None:
            pos_request = (
                await db.execute(
                    select(PosRefundRequest)
                    .where(PosRefundRequest.id == int(pos_refund_request_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                pos_request is None
                or int(pos_request.order_id) != int(payment.order_id or 0)
                or pos_request.status not in ("PROCESSING", "FAILED")
            ):
                raise AppException(40954, "POS 退款申请状态已变化，无法绑定渠道退款单")
            # 在调用渠道前先建立精确关联，避免极快的异步 SUCCESS 回调早于 POS refund_id 落库。
            pos_request.refund_id = row.id
            pos_request.payment_id = payment.id
            pos_request.status = "PROCESSING"
            pos_request.fail_reason = None

        # 短事务：落库 PENDING 退款单和 POS 关联并释放行锁后再调第三方
        refund_id = row.id
        payment_id = payment.id
        await db.commit()

        provider_inst = cls.get_provider(payment.provider) if (payment.provider or "").upper() not in ("CASH", "BALANCE") else None
        try:
            if provider_inst is None:
                result = {
                    "refund_no": refund_no,
                    "status": "SUCCESS",
                    "amount": str(amount),
                    "provider_refund_no": (
                        f"bal_rf_{refund_no}"
                        if (payment.provider or "").upper() == "BALANCE"
                        else f"cash_rf_{refund_no}"
                    ),
                    "_mock": False,
                }
            else:
                payment = await db.get(Payment, payment_id)
                if not payment:
                    raise AppException(40401, "支付记录不存在")
                result = await provider_inst.refund_payment(payment, amount, reason, refund_no)
        except AppException as e:
            # 只有本地配置/签名材料缺失（请求尚未发出）才可判定为确定失败。
            # 渠道返回的 4xx/5xx 业务错误也可能发生在受理之后，保守置 UNKNOWN 并查单，
            # 绝不能因一个 AppException 就把可重试退款单标 FAILED 后再次发起退款。
            # Even a deterministic provider/configuration error must follow the
            # same aggregate lock order before mutating the persisted refund row.
            current_payment = await cls._lock_order_then_payment(db, int(payment_id))
            if current_payment is None:
                raise AppException(40401, "支付记录不存在")
            row = (
                await db.execute(
                    select(PaymentRefund)
                    .where(PaymentRefund.id == refund_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if e.code in (50010, 50011, 50020):
                if row and row.status in ("PENDING", "PROCESSING", "UNKNOWN"):
                    row.status = "FAILED"
                    row.fail_reason = str(e)[:255]
                    await db.flush()
                    await db.commit()
                raise
            if row and row.status in ("PENDING", "PROCESSING", "UNKNOWN"):
                row.status = "UNKNOWN"
                row.fail_reason = f"渠道返回结果不确定，待查单补偿: {e}"[:255]
                await db.flush()
                await db.commit()
                logger.warning("退款渠道返回不确定错误，保留原退款单 refund_no=%s: %s", refund_no, e)
                return row
            raise
        except Exception as e:
            # 网络超时/结果未知：渠道侧可能已实际退款，置 UNKNOWN 并交由
            # poll_pending_refunds 定时查单补偿，防止双重退款
            current_payment = await cls._lock_order_then_payment(db, int(payment_id))
            if current_payment is None:
                raise AppException(40401, "支付记录不存在")
            row = (
                await db.execute(
                    select(PaymentRefund)
                    .where(PaymentRefund.id == refund_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row and row.status in ("PENDING", "PROCESSING"):
                row.status = "UNKNOWN"
                row.fail_reason = f"退款结果未知，待查单补偿: {e}"[:255]
                await db.flush()
                await db.commit()
            logger.error("退款申请结果未知 refund_no=%s: %s", refund_no, e)
            # 将已持久化的 UNKNOWN 退款单返回调用方，使 POS 审批单能绑定 refund_id；
            # 后续回调/查单成功时，_sync_pos_refund_request_on_success 才能精确补齐累计金额。
            if row is not None:
                return row
            raise AppException(40055, "退款结果未知，系统将自动查询退款结果，请稍后查看退款状态")

        # Reacquire parent locks before the refund row after the provider call.
        # The network request above is intentionally outside every DB row lock.
        current_payment = await cls._lock_order_then_payment(db, int(payment_id))
        if current_payment is None:
            raise AppException(40401, "支付记录不存在")
        row = (
            await db.execute(
                select(PaymentRefund)
                .where(PaymentRefund.id == refund_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40405, "退款单不存在")
        if row.status == "SUCCESS":
            return row

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
            # 受理成功，等待回调/定时查询——不得当作已退款
            row.status = "PROCESSING"
            await db.flush()

        return row

    @classmethod
    async def _query_channel_refund(
        cls,
        *,
        provider: str,
        payment_no: str,
        refund_no: str,
    ) -> dict | None:
        """向渠道查退款（无 DB 锁）。返回 None 表示未配置/不支持。"""
        provider_u = (provider or "").upper()
        if provider_u == "WECHAT":
            if not _wechat_pay_configured():
                return None
            return await wechat_pay_client.query_refund(refund_no=refund_no)
        if provider_u == "ALIPAY":
            if not _alipay_pay_configured():
                return None
            return await alipay_client.trade_refund_query(
                payment_no=payment_no, refund_no=refund_no
            )
        return None

    @classmethod
    def _validate_refund_query_result(
        cls, *, provider: str, payment: Payment, refund: PaymentRefund, result: dict
    ) -> str:
        """统一校验主动查单与定时补偿的渠道退款结果；只有完整匹配才允许入账。"""
        provider_u = str(provider or "").strip().upper()
        if provider_u not in ("WECHAT", "ALIPAY"):
            raise AppException(40302, "退款查询渠道与本地支付记录不匹配")
        if (
            int(refund.payment_id) != int(payment.id)
            or not payment.payment_no
            or not refund.refund_no
        ):
            raise AppException(40302, "退款单缺少有效的原支付/退款编号")
        refund_order_id = getattr(refund, "order_id", None)
        payment_order_id = getattr(payment, "order_id", None)
        if (refund_order_id is None) != (payment_order_id is None):
            raise AppException(40302, "退款单与原支付订单关联不一致")
        if refund_order_id is not None and int(refund_order_id) != int(payment_order_id):
            raise AppException(40302, "退款单与原支付订单关联不一致")

        result_refund_no = str(result.get("refund_no") or "").strip()
        if result_refund_no and result_refund_no != str(refund.refund_no):
            raise AppException(40302, "渠道退款查询返回的退款单号不匹配")
        result_payment_no = str(result.get("payment_no") or "").strip()
        if result_payment_no and result_payment_no != str(payment.payment_no):
            raise AppException(40302, "渠道退款查询返回的原支付单号不匹配")
        result_trade_no = str(result.get("provider_trade_no") or "").strip()
        local_trade_no = str(payment.provider_trade_no or "").strip()
        if result_trade_no and (not local_trade_no or result_trade_no != local_trade_no):
            raise AppException(40302, "渠道退款查询返回的原交易号不匹配")

        query_status = str(result.get("status") or "PROCESSING").strip().upper()
        if query_status == "SUCCESS":
            if provider_u == "ALIPAY":
                amount_raw = result.get("amount")
                if amount_raw is None:
                    raise AppException(50302, "支付宝退款查询缺少本次退款金额，拒绝入账")
                try:
                    query_amount = money(Decimal(str(amount_raw)))
                except (InvalidOperation, ValueError) as exc:
                    raise AppException(50302, "支付宝退款查询金额解析失败") from exc
                if query_amount != money(refund.amount):
                    raise AppException(40302, "支付宝退款查询本次退款金额与本地申请不匹配")
            else:
                amount_info = result.get("amount")
                if not isinstance(amount_info, dict):
                    raise AppException(50302, "微信退款查询缺少金额信息，拒绝入账")
                refund_raw = amount_info.get("refund")
                total_raw = amount_info.get("total")
                if refund_raw is None or total_raw is None:
                    raise AppException(50302, "微信退款查询缺少本次退款或原订单金额")
                try:
                    query_refund_amount = money(Decimal(str(refund_raw)) / 100)
                    query_total_amount = money(Decimal(str(total_raw)) / 100)
                except (InvalidOperation, ValueError) as exc:
                    raise AppException(50302, "微信退款查询金额解析失败") from exc
                if query_refund_amount != money(refund.amount):
                    raise AppException(40302, "微信退款查询本次退款金额与本地申请不匹配")
                if query_total_amount != money(payment.amount):
                    raise AppException(40302, "微信退款查询原订单总金额与本地支付金额不匹配")
                if str(amount_info.get("currency") or "CNY").upper() != "CNY":
                    raise AppException(40302, "微信退款查询币种必须为 CNY")
        return query_status

    @classmethod
    async def sync_refund_status(cls, db: AsyncSession, row: PaymentRefund) -> PaymentRefund:
        """主动查询平台退款结果（补偿回调丢失）。"""
        if row.status == "SUCCESS":
            return row
        payment = await db.get(Payment, row.payment_id)
        if not payment:
            raise AppException(40401, "支付记录不存在")
        provider = str(payment.provider or "").strip().upper()
        if provider not in ("WECHAT", "ALIPAY"):
            raise AppException(40302, "退款查询渠道与本地支付记录不匹配")
        if not payment.payment_no or not row.refund_no or int(row.payment_id) != int(payment.id):
            raise AppException(40302, "退款单缺少有效的原支付/退款编号")
        refund_order_id = getattr(row, "order_id", None)
        payment_order_id = getattr(payment, "order_id", None)
        if (refund_order_id is None) != (payment_order_id is None):
            raise AppException(40302, "退款单与原支付订单关联不一致")
        if refund_order_id is not None and int(refund_order_id) != int(payment_order_id):
            raise AppException(40302, "退款单与原支付订单关联不一致")

        result = await cls._query_channel_refund(
            provider=provider,
            payment_no=str(payment.payment_no),
            refund_no=str(row.refund_no),
        )
        if result is None:
            return row

        # 主动查单与定时补偿共用同一套渠道身份、订单关联、金额和币种校验。
        query_status = cls._validate_refund_query_result(
            provider=provider, payment=payment, refund=row, result=result
        )

        # HTTP 查单期间可能有异步回调/另一个补偿 Worker 已完成退款。
        # 必须在网络请求结束后重新当前读并锁定退款行，不能用请求前的 ORM 快照
        # 覆盖并发事务写入的 SUCCESS/FAILED 等状态，也不能重复执行财务分录。
        # Match callback and refund creation lock order: parent payment aggregate
        # first, then the individual refund row. Provider HTTP above holds no locks.
        locked_payment = await cls._lock_order_then_payment(db, int(payment.id))
        if locked_payment is None:
            raise AppException(40401, "支付记录不存在")
        current_row = (
            await db.execute(
                select(PaymentRefund)
                .where(PaymentRefund.id == int(row.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if current_row is None:
            raise AppException(40403, "退款记录不存在")
        if (
            int(current_row.payment_id) != int(payment.id)
            or str(current_row.refund_no or "") != str(row.refund_no or "")
            or money(current_row.amount) != money(row.amount)
            or (
                current_row.order_id is not None
                and payment.order_id is not None
                and int(current_row.order_id) != int(payment.order_id)
            )
        ):
            raise AppException(40302, "退款记录在查单期间发生关联或金额变化，拒绝入账")
        # 成功是不可逆终态；并发回调已完成时直接返回，避免重复财务/积分/钱包副作用。
        if current_row.status == "SUCCESS":
            return current_row

        return await cls._finalize_refund(
            db,
            current_row,
            query_status,
            provider_refund_no=result.get("provider_refund_no"),
            callback_raw={"query": result.get("raw")},
            source="REFUND_QUERY",
        )

    @classmethod
    async def poll_pending_payments(cls, limit: int = 30, *, min_age_seconds: int = 30) -> int:
        """定时补偿：扫描 PENDING/近期 CLOSED 的微信/支付宝支付单并主动查单入账。

        min_age_seconds：跳过刚创建的单，避免与用户正在收银台付款竞态。
        CLOSED：覆盖关单盲置后渠道迟到成功（默认回溯 48 小时）。

        高并发要点：只捞 id（不加长锁）→ 渠道 HTTP 在锁外 → 入账短事务加锁。
        避免一次 FOR UPDATE 锁住多行再串行调微信/支付宝，拖慢其它顾客。
        """
        from datetime import timedelta

        from app.core.response import naive_local_now

        now = naive_local_now()
        cutoff = now - timedelta(seconds=max(0, int(min_age_seconds)))
        closed_since = now - timedelta(hours=48)
        async with AsyncSessionLocal() as db:
            ids = list(
                (
                    await db.execute(
                        select(Payment.id)
                        .where(
                            Payment.provider.in_(("WECHAT", "ALIPAY")),
                            or_(
                                and_(
                                    Payment.status == "PENDING",
                                    Payment.created_at <= cutoff,
                                ),
                                and_(
                                    Payment.status == "CLOSED",
                                    Payment.updated_at >= closed_since,
                                ),
                            ),
                        )
                        .order_by(Payment.id.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        done = 0
        for pid in ids:
            try:
                # 1) 短锁：跳过已终态 / 被其它 worker 占用的行，拷贝查单字段后立刻释放
                async with AsyncSessionLocal() as db:
                    row = (
                        await db.execute(
                            select(Payment)
                            .where(
                                Payment.id == int(pid),
                                Payment.status.in_(("PENDING", "CLOSED")),
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        continue
                    before = row.status
                    from types import SimpleNamespace

                    snap = SimpleNamespace(
                        id=row.id,
                        payment_no=row.payment_no,
                        status=row.status,
                        provider=row.provider,
                        channel=row.channel,
                        amount=row.amount,
                        order_id=row.order_id,
                        recharge_order_id=row.recharge_order_id,
                    )
                    await db.commit()

                # 2) 渠道查单：无持有 DB 行锁
                try:
                    channel = await cls._query_channel_trade(snap)
                except Exception as e:
                    logger.warning("支付查单失败 payment_no=%s: %s", snap.payment_no, e)
                    continue

                if channel.get("error"):
                    continue
                if not channel.get("paid"):
                    state = channel.get("trade_state")
                    if state not in (
                        None,
                        "NOTPAY",
                        "USERPAYING",
                        "WAIT_BUYER_PAY",
                        "NOT_CONFIGURED",
                        "QUERY_FAILED",
                        "UNKNOWN",
                        "CLOSED",
                    ):
                        logger.info(
                            "支付查单未入账 payment=%s state=%s",
                            snap.payment_no,
                            state,
                        )
                    continue

                # 3) 短锁入账
                async with AsyncSessionLocal() as db:
                    payment = await cls._lock_order_then_payment(db, int(pid))
                    if payment is None or payment.status == "PAID":
                        await db.commit()
                        continue
                    if payment.status not in ("PENDING", "CLOSED"):
                        await db.commit()
                        continue
                    await cls._settle_from_query(
                        db,
                        payment,
                        provider=str(channel["provider"]),
                        provider_trade_no=str(channel.get("provider_trade_no") or ""),
                        query_raw=channel.get("query_raw"),
                    )
                    await db.commit()
                    if before != "PAID" and payment.status == "PAID":
                        done += 1
                        try:
                            from app.services.websocket_manager import ws_manager

                            order = await db.get(Order, payment.order_id)
                            await ws_manager.notify_order_event(
                                "ORDER_PAID",
                                {
                                    "order_id": payment.order_id,
                                    "order_no": order.order_no if order else None,
                                    "store_id": order.store_id if order else None,
                                    "source": "PAYMENT_POLL",
                                },
                            )
                        except Exception as ws_err:
                            logger.warning("支付查单成功 WS 通知失败: %s", ws_err)
            except Exception as e:
                logger.warning("支付查单补偿异常 payment_id=%s: %s", pid, e)
        return done

    @classmethod
    async def sync_before_close_order(cls, db: AsyncSession, order: Order) -> bool:
        """关单前对订单下 PENDING 渠道支付单查单；若已付返回 True（不应再关单）。

        注意：若调用方已持有 order 行锁，查单期间该锁仍会被占用。
        定时关单请用 sync_before_close_order_by_id（独立短事务）。
        """
        await db.refresh(order)
        if order.payment_status == "PAID" or order.status != "PENDING_PAYMENT":
            return order.payment_status == "PAID"
        pending = list(
            (
                await db.execute(
                    select(Payment).where(
                        Payment.order_id == order.id,
                        Payment.status == "PENDING",
                        Payment.provider.in_(("WECHAT", "ALIPAY")),
                    )
                )
            ).scalars()
        )
        for payment in pending:
            try:
                payment, _meta = await cls.sync_payment_status(db, payment)
                if payment.status == "PAID":
                    return True
            except Exception as e:
                logger.warning(
                    "关单前查单失败 order=%s payment=%s: %s",
                    order.order_no,
                    payment.payment_no,
                    e,
                )
        await db.refresh(order)
        return order.payment_status == "PAID"

    @classmethod
    async def sync_before_close_order_by_id(cls, order_id: int) -> bool:
        """关单前查单（独立会话）：不持有订单长锁做渠道 HTTP。"""
        async with AsyncSessionLocal() as db:
            order = await db.get(Order, int(order_id))
            if not order:
                return False
            if order.payment_status == "PAID":
                return True
            if order.status != "PENDING_PAYMENT":
                return order.payment_status == "PAID"
            payment_ids = list(
                (
                    await db.execute(
                        select(Payment.id).where(
                            Payment.order_id == int(order_id),
                            Payment.status == "PENDING",
                            Payment.provider.in_(("WECHAT", "ALIPAY")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            order_no = order.order_no
            await db.commit()

        for pid in payment_ids:
            try:
                async with AsyncSessionLocal() as db:
                    payment = await db.get(Payment, int(pid))
                    if not payment or payment.status != "PENDING":
                        await db.commit()
                        continue
                    payment, _meta = await cls.sync_payment_status(db, payment)
                    await db.commit()
                    if payment.status == "PAID":
                        return True
            except Exception as e:
                logger.warning(
                    "关单前查单失败 order=%s payment_id=%s: %s",
                    order_no,
                    pid,
                    e,
                )

        async with AsyncSessionLocal() as db:
            order = await db.get(Order, int(order_id))
            return bool(order and order.payment_status == "PAID")

    @classmethod
    async def close_channel_payments(cls, db: AsyncSession, order: Order) -> bool:
        """超时关单时同步撤销渠道交易，并把本地 PENDING 支付单置 CLOSED。

        P1 修复：原逻辑只置订单 CANCELLED 但不调渠道关单，导致渠道交易悬挂、
        顾客迟到成功支付后资金无法回收。现流程：
        1. sync_before_close_order 已查单确认未付（sync_payment_status 会自动入账）
        2. 调渠道关单/撤销接口，撤销渠道侧交易
        3. 本地 PENDING 支付单置 CLOSED
        4. 关单后再查一次单，若发现迟到成功支付（sync_payment_status 已入账 PAID）
           返回 True，由上层保留订单不关单

        返回 True 表示检测到迟到成功支付（订单已入账，不应关单）。
        定时关单请用 close_channel_payments_by_id（渠道 HTTP 在订单锁外）。
        """
        await db.refresh(order)
        if order.payment_status == "PAID":
            return True
        pending = list(
            (
                await db.execute(
                    select(Payment).where(
                        Payment.order_id == order.id,
                        Payment.status == "PENDING",
                        Payment.provider.in_(("WECHAT", "ALIPAY")),
                    )
                )
            ).scalars()
        )
        late_paid = False
        for payment in pending:
            provider_u = (payment.provider or "").upper()
            try:
                # 1. 调渠道关单/撤销
                if provider_u == "WECHAT" and _wechat_pay_configured():
                    await wechat_pay_client.close_order(payment_no=payment.payment_no)
                elif provider_u == "ALIPAY" and _alipay_pay_configured():
                    await alipay_client.trade_cancel(payment_no=payment.payment_no)
                # 2. 关单后再查一次单，确认渠道状态（防止关单与支付竞态）
                synced, _meta = await cls.sync_payment_status(db, payment)
                if synced.status == "PAID":
                    late_paid = True
                    logger.warning(
                        "渠道关单后发现迟到成功支付 order=%s payment=%s，已自动入账",
                        order.order_no,
                        payment.payment_no,
                    )
                    continue
                # 3. 本地支付单置 CLOSED
                await cls._close_pending_payment(
                    db, payment, reason="超时关单同步撤销渠道交易"
                )
            except Exception as e:
                logger.warning(
                    "关单撤销渠道交易失败 order=%s payment=%s: %s",
                    order.order_no,
                    payment.payment_no,
                    e,
                )
                # 失败仍置本地 CLOSED，由 poll_pending_payments 兜底查单
                await cls._close_pending_payment(
                    db, payment, reason=f"关单撤销失败: {e}"[:200]
                )
        return late_paid

    @classmethod
    async def close_channel_payments_by_id(cls, order_id: int) -> bool:
        """撤销渠道交易（独立会话）：HTTP 不持订单行锁。"""
        from types import SimpleNamespace

        async with AsyncSessionLocal() as db:
            order = await db.get(Order, int(order_id))
            if not order:
                return False
            if order.payment_status == "PAID":
                return True
            rows = list(
                (
                    await db.execute(
                        select(Payment).where(
                            Payment.order_id == int(order_id),
                            Payment.status == "PENDING",
                            Payment.provider.in_(("WECHAT", "ALIPAY")),
                        )
                    )
                ).scalars()
            )
            snaps = [
                SimpleNamespace(
                    id=int(p.id),
                    payment_no=p.payment_no,
                    provider=(p.provider or "").upper(),
                )
                for p in rows
            ]
            order_no = order.order_no
            await db.commit()

        late_paid = False
        for snap in snaps:
            try:
                if snap.provider == "WECHAT" and _wechat_pay_configured():
                    await wechat_pay_client.close_order(payment_no=snap.payment_no)
                elif snap.provider == "ALIPAY" and _alipay_pay_configured():
                    await alipay_client.trade_cancel(payment_no=snap.payment_no)
            except Exception as e:
                logger.warning(
                    "关单撤销渠道交易失败 order=%s payment=%s: %s",
                    order_no,
                    snap.payment_no,
                    e,
                )

            try:
                async with AsyncSessionLocal() as db:
                    payment = await db.get(Payment, int(snap.id))
                    if not payment:
                        await db.commit()
                        continue
                    synced, _meta = await cls.sync_payment_status(db, payment)
                    await db.commit()
                    if synced.status == "PAID":
                        late_paid = True
                        logger.warning(
                            "渠道关单后发现迟到成功支付 order=%s payment=%s，已自动入账",
                            order_no,
                            snap.payment_no,
                        )
                        continue
            except Exception as e:
                logger.warning(
                    "关单后查单失败 order=%s payment=%s: %s",
                    order_no,
                    snap.payment_no,
                    e,
                )

            try:
                async with AsyncSessionLocal() as db:
                    payment = await cls._lock_order_then_payment(db, int(snap.id))
                    if payment is not None and payment.status == "PENDING":
                        await cls._close_pending_payment(
                            db,
                            payment,
                            reason="超时关单同步撤销渠道交易",
                        )
                    await db.commit()
            except Exception as e:
                logger.warning(
                    "本地置 CLOSED 失败 order=%s payment=%s: %s",
                    order_no,
                    snap.payment_no,
                    e,
                )

        return late_paid

    @classmethod
    async def poll_pending_refunds(cls, limit: int = 20) -> int:
        """定时补偿：拉取未决退款；与主动查单共用严格的渠道结果校验。"""
        from types import SimpleNamespace

        async with AsyncSessionLocal() as db:
            ids = list(
                (
                    await db.execute(
                        select(PaymentRefund.id)
                        .where(PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")))
                        .order_by(PaymentRefund.id.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        done = 0
        for rid in ids:
            try:
                # 第一阶段只读取快照，随后立即提交释放连接/锁；绝不持锁调用渠道 HTTP。
                async with AsyncSessionLocal() as db:
                    row = (
                        await db.execute(
                            select(PaymentRefund)
                            .where(
                                PaymentRefund.id == int(rid),
                                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL")),
                            )
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        continue
                    before = row.status
                    payment = await db.get(Payment, row.payment_id)
                    if not payment:
                        await db.commit()
                        continue
                    refund_snapshot = SimpleNamespace(
                        id=int(row.id),
                        payment_id=int(row.payment_id),
                        order_id=row.order_id,
                        refund_no=str(row.refund_no or ""),
                        amount=row.amount,
                    )
                    payment_snapshot = SimpleNamespace(
                        id=int(payment.id),
                        provider=str(payment.provider or "").strip().upper(),
                        payment_no=str(payment.payment_no or ""),
                        provider_trade_no=str(payment.provider_trade_no or ""),
                        order_id=payment.order_id,
                        amount=payment.amount,
                    )
                    await db.commit()

                try:
                    result = await cls._query_channel_refund(
                        provider=payment_snapshot.provider,
                        payment_no=payment_snapshot.payment_no,
                        refund_no=refund_snapshot.refund_no,
                    )
                except Exception as e:
                    logger.warning("退款查询失败 refund_no=%s: %s", refund_snapshot.refund_no, e)
                    continue
                if result is None:
                    continue

                # 定时任务不得绕过主动查单的渠道身份、订单、金额和币种校验。
                query_status = cls._validate_refund_query_result(
                    provider=payment_snapshot.provider,
                    payment=payment_snapshot,
                    refund=refund_snapshot,
                    result=result,
                )

                async with AsyncSessionLocal() as db:
                    # All writers acquire Order→Payment→PaymentRefund. Do not
                    # lock the refund row first and then wait for its payment.
                    current_payment = await cls._lock_order_then_payment(
                        db, int(refund_snapshot.payment_id)
                    )
                    if current_payment is None:
                        await db.rollback()
                        continue
                    row = (
                        await db.execute(
                            select(PaymentRefund)
                            .where(PaymentRefund.id == int(rid))
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None or row.status == "SUCCESS":
                        await db.commit()
                        continue
                    if (
                        int(row.payment_id) != refund_snapshot.payment_id
                        or str(row.refund_no or "") != refund_snapshot.refund_no
                        or money(row.amount) != money(refund_snapshot.amount)
                        or row.order_id != refund_snapshot.order_id
                    ):
                        await db.rollback()
                        logger.error("退款查单期间本地退款单发生变化，拒绝应用结果 refund_id=%s", rid)
                        continue

                    if query_status == "SUCCESS":
                        # 资金入账前按统一 Order→Payment 顺序锁定并重新核对原支付快照。
                        current_payment, _ = await cls._lock_order_then_payment_for_refund(db, row)
                    else:
                        current_payment = await db.get(
                            Payment, row.payment_id, populate_existing=True
                        )
                    if not current_payment or (
                        str(current_payment.provider or "").strip().upper() != payment_snapshot.provider
                        or str(current_payment.payment_no or "") != payment_snapshot.payment_no
                        or str(current_payment.provider_trade_no or "") != payment_snapshot.provider_trade_no
                        or current_payment.order_id != payment_snapshot.order_id
                        or money(current_payment.amount) != money(payment_snapshot.amount)
                    ):
                        await db.rollback()
                        logger.error("退款查单期间原支付记录发生变化，拒绝应用结果 refund_id=%s", rid)
                        continue

                    await cls._finalize_refund(
                        db,
                        row,
                        query_status,
                        provider_refund_no=result.get("provider_refund_no"),
                        callback_raw={"query": result.get("raw")},
                        source="REFUND_QUERY",
                    )
                    await db.commit()
                    if before != "SUCCESS" and row.status == "SUCCESS":
                        done += 1
                        try:
                            from app.services.websocket_manager import ws_manager

                            order = await db.get(Order, row.order_id)
                            await ws_manager.notify_order_event(
                                "ORDER_REFUND",
                                {
                                    "order_id": row.order_id,
                                    "order_no": order.order_no if order else None,
                                    "status": order.status if order else None,
                                    "payment_status": order.payment_status if order else None,
                                    "refund_no": row.refund_no,
                                    "store_id": order.store_id if order else None,
                                },
                            )
                        except Exception as ws_err:
                            logger.warning("退款成功 WS 通知失败: %s", ws_err)
            except Exception as e:
                logger.warning("退款查单补偿异常 refund_id=%s: %s", rid, e)
        return done
