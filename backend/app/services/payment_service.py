"""支付服务：真实创建/退款 + 验签回调 + 金额/商户校验 + 防重放 + 状态机"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.biz_no import generate_no, payment_biz, refund_biz, resolve_table_parts
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import DiningTable, Order, Payment, PaymentCallbackReceipt, PaymentRefund, Store
from app.services import alipay_client, wechat_pay_client
from app.services.order_lifecycle import apply_order_status
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.status_history import record_payment_status
from app.services.webhook_replay import claim_once, make_receipt_key, release_claim

logger = logging.getLogger(__name__)


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
            "amount": total,
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
        # 退款异步：有 refund_fee / gmt_refund；用 out_biz_no 对齐 out_request_no
        is_refund = bool(data.get("refund_fee") or data.get("gmt_refund"))
        return {
            "payment_no": data.get("out_trade_no"),
            "provider_trade_no": data.get("trade_no"),
            "amount": data.get("total_amount", "0"),
            "currency": data.get("currency") or "CNY",
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
        """订单已成功入账金额（PAID 支付单合计）。"""
        total = await db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.order_id == int(order_id),
                Payment.status == "PAID",
            )
        )
        return money(total or 0)

    @classmethod
    async def remaining_amount(cls, db: AsyncSession, order: Order) -> Decimal:
        rem = money(order.total_amount) - await cls.paid_amount(db, order.id)
        return rem if rem > 0 else money(0)

    @classmethod
    async def list_paid_tenders(cls, db: AsyncSession, order_id: int) -> list[dict]:
        rows = (
            await db.execute(
                select(Payment)
                .where(Payment.order_id == int(order_id), Payment.status == "PAID")
                .order_by(Payment.id.asc())
            )
        ).scalars().all()
        out = []
        for p in rows:
            out.append(
                {
                    "payment_no": p.payment_no,
                    "provider": p.provider,
                    "channel": p.channel,
                    "amount": str(money(p.amount)),
                    "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                }
            )
        return out

    @classmethod
    async def payment_progress(cls, db: AsyncSession, order: Order) -> dict:
        paid = await cls.paid_amount(db, order.id)
        remaining = await cls.remaining_amount(db, order)
        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "total_amount": str(money(order.total_amount)),
            "paid_amount": str(paid),
            "remaining_amount": str(remaining),
            "payment_status": order.payment_status,
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
        return "MIXED"

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
        """本笔支付单已标 PAID 后：未结清→PARTIAL_PAID；结清→订单 PAID。每笔 SUCCESS 记 SALE。"""
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
        if order.status != "PENDING_PAYMENT":
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
            # 渠道已受理、待回调：标记 REQUESTING（若列/状态支持）
            if payment.status == "PENDING":
                payment.status = "PENDING"
        progress = await cls.payment_progress(db, order)
        pay_data.update(progress)
        return payment, pay_data

    @classmethod
    def _is_duplicate_out_trade_error(cls, exc: AppException) -> bool:
        msg = str(getattr(exc, "message", "") or exc or "")
        needles = ("商户订单号", "out_trade_no", "ORDERPAID", "FREQUENCY_LIMITED", "已存在", "重复")
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
    async def sync_payment_status(
        cls, db: AsyncSession, payment: Payment
    ) -> tuple[Payment, dict]:
        """主动向微信/支付宝查单并幂等入账（补偿回调丢失、支付结果不明）。"""
        locked = (
            await db.execute(select(Payment).where(Payment.id == payment.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40401, "支付记录不存在")
        payment = locked
        if payment.status == "PAID":
            return payment, {"synced": False, "trade_state": "SUCCESS", "already_paid": True}
        if payment.status in ("CLOSED", "REFUNDED", "PARTIAL_REFUND"):
            return payment, {"synced": False, "trade_state": payment.status}

        provider_u = (payment.provider or "").upper()
        if provider_u == "CASH":
            return payment, {"synced": False, "trade_state": payment.status}
        if provider_u == "WECHAT":
            if not (_wechat_pay_configured() or _wechat_micropay_configured()):
                return payment, {"synced": False, "trade_state": "NOT_CONFIGURED"}
            channel = (payment.channel or "").upper()
            if "MICROPAY" in channel:
                from app.services import wechat_pay_v2_client

                q = await wechat_pay_v2_client.order_query(payment_no=payment.payment_no)
                trade_state = str(q.get("trade_state") or "").upper()
                trade_no = q.get("transaction_id")
                amount_ok = True
                raw_amount = None
                if isinstance(q.get("raw"), dict) and q["raw"].get("total_fee"):
                    raw_amount = money(Decimal(str(q["raw"]["total_fee"])) / 100)
                    amount_ok = raw_amount == money(payment.amount)
            else:
                if not _wechat_pay_configured():
                    return payment, {"synced": False, "trade_state": "NOT_CONFIGURED"}
                q = await wechat_pay_client.query_by_out_trade_no(payment_no=payment.payment_no)
                trade_state = str(q.get("trade_state") or "").upper()
                trade_no = q.get("transaction_id")
                fen = q.get("amount_total_fen")
                raw_amount = money(Decimal(str(fen)) / 100) if fen is not None else None
                amount_ok = raw_amount is None or raw_amount == money(payment.amount)
            if trade_state == "SUCCESS":
                if not amount_ok:
                    raise AppException(40302, "支付金额不匹配")
                await cls._settle_from_query(
                    db,
                    payment,
                    provider="WECHAT",
                    provider_trade_no=str(trade_no or ""),
                    query_raw=q.get("raw") if isinstance(q, dict) else q,
                )
                return payment, {"synced": True, "trade_state": "SUCCESS", "status": "PAID"}
            return payment, {"synced": False, "trade_state": trade_state or "UNKNOWN"}

        if provider_u == "ALIPAY":
            if not _alipay_pay_configured():
                return payment, {"synced": False, "trade_state": "NOT_CONFIGURED"}
            q = await alipay_client.trade_query(payment_no=payment.payment_no)
            trade_status = str(q.get("trade_status") or "").upper()
            if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
                total = q.get("total_amount")
                if total is not None and money(Decimal(str(total))) != money(payment.amount):
                    raise AppException(40302, "支付金额不匹配")
                await cls._settle_from_query(
                    db,
                    payment,
                    provider="ALIPAY",
                    provider_trade_no=str(q.get("trade_no") or ""),
                    query_raw=q.get("raw") if isinstance(q, dict) else q,
                )
                return payment, {"synced": True, "trade_state": trade_status, "status": "PAID"}
            return payment, {"synced": False, "trade_state": trade_status or "UNKNOWN"}

        return payment, {"synced": False, "trade_state": "UNSUPPORTED"}

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
        order = (
            await db.execute(select(Order).where(Order.id == payment.order_id).with_for_update())
        ).scalar_one_or_none()
        if not order:
            raise AppException(40402, "订单不存在")
        if order.status == "CANCELLED" or order.payment_status == "CANCELLED":
            raise AppException(40901, "订单已关闭，拒绝支付入账")
        if order.payment_status == "PAID" and payment.status == "PENDING":
            old_p = payment.status
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
        """按订单同步最近一笔待支付单；已结清则直接返回。"""
        locked = (
            await db.execute(select(Order).where(Order.id == order.id).with_for_update())
        ).scalar_one_or_none()
        if not locked:
            raise AppException(40402, "订单不存在")
        order = locked
        if order.payment_status == "PAID":
            return None, {
                "synced": False,
                "already_paid": True,
                "payment_status": "PAID",
                "order_status": order.status,
            }
        q = (
            select(Payment)
            .where(Payment.order_id == order.id, Payment.status == "PENDING")
            .order_by(Payment.id.desc())
            .limit(1)
        )
        if provider:
            q = q.where(Payment.provider == provider.upper())
        payment = (await db.execute(q)).scalar_one_or_none()
        if not payment:
            progress = await cls.payment_progress(db, order)
            return None, {"synced": False, "trade_state": "NO_PENDING", **progress}
        payment, meta = await cls.sync_payment_status(db, payment)
        progress = await cls.payment_progress(db, order)
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
        if order.status != "PENDING_PAYMENT":
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
            callback_mchid = str(parsed_probe.get("mchid") or "").strip()
            expected_mchid = str(get_settings().wechat_mch_id or "").strip()
            if expected_mchid and callback_mchid and callback_mchid != expected_mchid:
                logger.warning("微信回调 mchid 不匹配: got=%s expected=%s", callback_mchid, expected_mchid)
                raise AppException(40054, "微信回调商户号不匹配，拒绝处理")
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
            # P0-10: 支付宝回调身份 fail-closed — app_id 必须匹配
            callback_appid = str(parsed_probe.get("app_id") or "").strip()
            expected_appid = str(get_settings().alipay_app_id or "").strip()
            if expected_appid and callback_appid and callback_appid != expected_appid:
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
        first = await claim_once(redis_channel, receipt)
        if not first:
            # Redis 显示已认领：可能是真重复，也可能是崩溃残留
            # （claim 后、receipt 提交前 crash）。必须查 DB receipt 判断真相，
            # 否则支付状态可能永久卡住（#2）。
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
                # 真重复：receipt 已落库，业务已处理过，短路返回现有对象
                if parsed_probe.get("is_refund"):
                    refund_no = parsed_probe.get("refund_no")
                    if refund_no:
                        return (
                            await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))
                        ).scalar_one_or_none()
                    return None
                result = await db.execute(select(Payment).where(Payment.payment_no == parsed_probe.get("payment_no")))
                return result.scalar_one_or_none()
            # Redis claim 是崩溃残留（receipt 未落库）：释放 Redis，继续处理，
            # 让本次回调真正入账，而不是被误判为重复。
            await release_claim(redis_channel, receipt)

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
            # DB UNIQUE 已认领：与 Redis 一致视为重复，不再处理
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
        except IntegrityError:
            # 业务级 UNIQUE 冲突（如已 PAID 订单再次标记、point_ledger/status_history 重复）：
            # 视为重复处理，回滚本次未提交变更，短路返回现有对象。
            # 不释放 Redis / 不删 receipt（receipt 未提交已随 rollback 消失；
            # 若 prior 已提交 receipt 仍在 DB 守护，重试会被上面的 receipt 检查短路）。
            try:
                await db.rollback()
            except Exception:
                pass
            if parsed_probe.get("is_refund"):
                refund_no = parsed_probe.get("refund_no")
                if refund_no:
                    return (
                        await db.execute(select(PaymentRefund).where(PaymentRefund.refund_no == refund_no))
                    ).scalar_one_or_none()
                return None
            result = await db.execute(select(Payment).where(Payment.payment_no == parsed_probe.get("payment_no")))
            return result.scalar_one_or_none()
        except Exception:
            # 瞬时/未知错误（OperationalError/Timeout 等）：回滚（receipt 未提交随之回滚），
            # 释放 Redis 让 provider 重试，re-raise 让 provider 知道重试。
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                async with AsyncSessionLocal() as db_clean:
                    existing = (
                        await db_clean.execute(
                            select(PaymentCallbackReceipt).where(
                                PaymentCallbackReceipt.provider == receipt_provider,
                                PaymentCallbackReceipt.receipt_key == receipt,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing:
                        await db_clean.delete(existing)
                        await db_clean.commit()
            except Exception as clean_err:
                logger.warning("回调失败后清理 DB 回执失败: %s", clean_err)
            await release_claim(redis_channel, receipt)
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
        if order.payment_status == "PAID":
            logger.warning(
                "重复支付回调已拒绝 order=%s payment=%s",
                order.order_no,
                payment.payment_no,
            )
            if payment.status == "PENDING":
                old_p = payment.status
                payment.status = "CLOSED"
                payment.fail_reason = "订单已支付，重复入账拒绝"
                await record_payment_status(
                    db, payment_id=payment.id, from_status=old_p, to_status="CLOSED", source="CALLBACK"
                )
            await db.flush()
            return payment

        settings = get_settings()
        if provider_u == "WECHAT":
            mch = parsed.get("mchid") or data.get("mchid")
            if mch and str(mch) != str(settings.wechat_mch_id):
                raise AppException(40301, "支付商户号不匹配")
        else:
            app_id = parsed.get("app_id") or data.get("app_id")
            if app_id and settings.alipay_app_id and str(app_id) != str(settings.alipay_app_id):
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
        if provider_u == "WECHAT":
            callback_amount = money(Decimal(str(raw_amount)) / 100)
        else:
            callback_amount = money(Decimal(str(raw_amount)))
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
                        PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN")),
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

        # P0-11: 退款金额严格校验 — provider refund_fee 必须匹配本地退款金额
        refund_fee_raw = parsed.get("refund_fee")
        if refund_fee_raw is not None:
            try:
                provider_refund_amount = money(Decimal(str(refund_fee_raw)))
                if provider_refund_amount != money(row.amount):
                    raise AppException(40302, "退款金额不匹配")
            except (InvalidOperation, ValueError):
                logger.warning("退款回调 refund_fee 解析失败: %s", refund_fee_raw)

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
        # P1-14 Refund state machine: PENDING -> PROCESSING -> {SUCCESS, FAILED, CLOSED, UNKNOWN}.
        # Idempotent: SUCCESS is terminal; re-finalizing a SUCCESS row is a no-op.
        # UNKNOWN = provider returned an ambiguous result; must NOT be promoted to SUCCESS;
        #   recovery happens via sync_refund_status query / scheduled compensation.
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
            row._callback_transitioned_to_success = True
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
                # 按订单净已收判断：分笔退满一笔不得误标整单 REFUNDED
                net = await cls.order_net_collected(db, order.id)
                total = money(order.total_amount)
                if net <= 0:
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
            row.status = new_status if new_status != "ABNORMAL" else "FAILED"
            row.fail_reason = row.fail_reason or fail_reason or new_status
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
        """可退余额 = 支付金额 − (成功+进行中+未知)退款。"""
        used = await db.scalar(
            select(func.coalesce(func.sum(PaymentRefund.amount), 0)).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("SUCCESS", "PENDING", "PROCESSING", "UNKNOWN")),
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

        # 进行中/未知的退款单：禁止再开一笔，避免连点重复提交
        inflight = await db.scalar(
            select(func.count()).select_from(PaymentRefund).where(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN")),
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

        # 短事务：落库 PENDING 退款单并释放行锁后再调第三方
        refund_id = row.id
        payment_id = payment.id
        await db.commit()

        provider_inst = cls.get_provider(payment.provider) if (payment.provider or "").upper() != "CASH" else None
        try:
            if provider_inst is None:
                result = {
                    "refund_no": refund_no,
                    "status": "SUCCESS",
                    "amount": str(amount),
                    "provider_refund_no": f"cash_rf_{refund_no}",
                    "_mock": False,
                }
            else:
                payment = await db.get(Payment, payment_id)
                if not payment:
                    raise AppException(40401, "支付记录不存在")
                result = await provider_inst.refund_payment(payment, amount, reason, refund_no)
        except Exception as e:
            row = (
                await db.execute(select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update())
            ).scalar_one_or_none()
            if row and row.status in ("PENDING", "PROCESSING"):
                row.status = "FAILED"
                row.fail_reason = str(e)[:255]
                await db.flush()
                await db.commit()
            raise

        row = (
            await db.execute(select(PaymentRefund).where(PaymentRefund.id == refund_id).with_for_update())
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
    async def poll_pending_payments(cls, limit: int = 30, *, min_age_seconds: int = 30) -> int:
        """定时补偿：扫描 PENDING 的微信/支付宝支付单并主动查单入账。

        min_age_seconds：跳过刚创建的单，避免与用户正在收银台付款竞态。
        """
        from datetime import timedelta

        from app.core.response import naive_local_now

        cutoff = naive_local_now() - timedelta(seconds=max(0, int(min_age_seconds)))
        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(Payment)
                        .where(
                            Payment.status == "PENDING",
                            Payment.provider.in_(("WECHAT", "ALIPAY")),
                            Payment.created_at <= cutoff,
                        )
                        .order_by(Payment.id.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            done = 0
            for row in rows:
                try:
                    before = row.status
                    payment, meta = await cls.sync_payment_status(db, row)
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
                    elif meta.get("trade_state") not in (
                        None,
                        "NOTPAY",
                        "USERPAYING",
                        "WAIT_BUYER_PAY",
                        "NOT_CONFIGURED",
                        "QUERY_FAILED",
                        "UNKNOWN",
                    ):
                        logger.info(
                            "支付查单未入账 payment=%s state=%s",
                            payment.payment_no,
                            meta.get("trade_state"),
                        )
                except Exception as e:
                    logger.warning("支付查单失败 payment_no=%s: %s", row.payment_no, e)
            await db.commit()
            return done

    @classmethod
    async def sync_before_close_order(cls, db: AsyncSession, order: Order) -> bool:
        """关单前对订单下 PENDING 渠道支付单查单；若已付返回 True（不应再关单）。"""
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
    async def poll_pending_refunds(cls, limit: int = 20) -> int:
        """定时补偿：拉取 PROCESSING/PENDING/UNKNOWN 退款单（query 恢复）。"""
        async with AsyncSessionLocal() as db:
            rows = list(
                (
                    await db.execute(
                        select(PaymentRefund)
                        .where(PaymentRefund.status.in_(("PENDING", "PROCESSING", "UNKNOWN")))
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
                    logger.warning("退款查询失败 refund_no=%s: %s", row.refund_no, e)
            await db.commit()
            return done
