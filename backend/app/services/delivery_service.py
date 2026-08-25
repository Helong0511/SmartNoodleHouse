"""外卖订单接入服务：推单落库 → 统一 Order → 厨房；取消/退款/催单/配送"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import generate_no
from app.core.exceptions import AppException
from app.core.response import money, now_local
from app.models import (
    DeliveryCallbackLog,
    DeliveryOrder,
    DeliveryProductMap,
    DeliveryRefund,
    Option,
    OptionGroup,
    Order,
    OrderItem,
    OrderItemOption,
    Product,
)
from app.services.delivery_context import PlatformCredentials, get_credentials_for_store, resolve_store_and_credentials
from app.services.delivery_events import (
    DeliveryEvent,
    classify_eleme_event,
    classify_meituan_event,
    parse_meituan_foods,
)
from app.services.delivery_providers import get_delivery_provider
from app.services.product_sync_service import ProductSyncService

logger = logging.getLogger(__name__)

STATUS_SYNC_MAP = {
    "CONFIRMED": "ACCEPTED",
    "PREPARING": "ACCEPTED",
    "READY": "READY",
    "COMPLETED": "DONE",
    "CANCELLED": "CANCELLED",
}


@dataclass
class DeliveryNotifyResult:
    event: str
    order: Order | None = None
    deferred_tasks: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class DeliveryService:
    @classmethod
    async def handle_notify(
        cls,
        db: AsyncSession,
        platform: str,
        headers: dict,
        body: bytes,
    ) -> DeliveryNotifyResult:
        settings = get_settings()
        notify_url = settings.meituan_notify_url if platform.upper() == "MEITUAN" else settings.eleme_notify_url
        provider = get_delivery_provider(platform)
        raw = provider.verify_notify(headers, body, notify_url=notify_url)

        event = classify_meituan_event(raw) if platform.upper() == "MEITUAN" else classify_eleme_event(raw)
        parsed = provider.parse_order(raw)
        parsed["event"] = event
        platform_order_id = parsed.get("platform_order_id") or ""

        store_id, credentials = await resolve_store_and_credentials(db, platform, raw, parsed)
        parsed["store_id"] = store_id
        provider = get_delivery_provider(platform, credentials)

        await cls._log_callback(db, platform, event, platform_order_id, None, raw)

        if event == DeliveryEvent.ORDER_CANCEL:
            order = await cls._handle_cancel(db, platform, platform_order_id, raw)
            return DeliveryNotifyResult(event=event, order=order)

        if event == DeliveryEvent.REFUND:
            order = await cls._handle_refund(db, platform, parsed, raw)
            return DeliveryNotifyResult(event=event, order=order, extra={"refund_pending": True})

        if event == DeliveryEvent.URGE:
            order, tasks = await cls._handle_urge(db, platform, platform_order_id, raw, credentials)
            return DeliveryNotifyResult(event=event, order=order, deferred_tasks=tasks, extra={"urge": True})

        if event == DeliveryEvent.DELIVERY:
            order = await cls._handle_delivery(db, platform, parsed, raw)
            return DeliveryNotifyResult(
                event=event,
                order=order,
                extra={
                    "logistics_status": parsed.get("logistics_status"),
                    "rider_name": parsed.get("rider_name"),
                },
            )

        if event == DeliveryEvent.ORDER_COMPLETED:
            order = await cls._handle_completed(db, platform, platform_order_id)
            return DeliveryNotifyResult(event=event, order=order)

        if event == DeliveryEvent.PRIVACY:
            order = await cls._handle_privacy(db, platform, parsed, raw)
            return DeliveryNotifyResult(event=event, order=order)

        if event == DeliveryEvent.ORDER_CONFIRMED:
            order = await cls._handle_confirmed(db, platform, platform_order_id)
            return DeliveryNotifyResult(event=event, order=order)

        if event not in (DeliveryEvent.ORDER_NEW, DeliveryEvent.UNKNOWN):
            logger.info("未处理的外卖事件 %s: %s", event, platform_order_id)
            return DeliveryNotifyResult(event=event)

        if not platform_order_id:
            raise AppException(40051, "缺少平台订单号")

        existing = await db.execute(
            select(DeliveryOrder).where(
                DeliveryOrder.platform == platform.upper(),
                DeliveryOrder.platform_order_id == platform_order_id,
            )
        )
        d_order = existing.scalar_one_or_none()
        if d_order:
            order = await db.get(Order, d_order.order_id)
            return DeliveryNotifyResult(event=DeliveryEvent.ORDER_NEW, order=order)

        if platform.upper() == "MEITUAN" and not parsed.get("items"):
            parsed["items"] = parse_meituan_foods(raw)

        order, tasks = await cls._create_from_platform(db, provider, parsed, raw, credentials)
        return DeliveryNotifyResult(event=DeliveryEvent.ORDER_NEW, order=order, deferred_tasks=tasks)

    @classmethod
    def _creds_dict(cls, credentials: PlatformCredentials | None) -> dict:
        if not credentials:
            return {}
        return {
            "store_id": credentials.store_id,
            "platform_shop_id": credentials.platform_shop_id,
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "extra": credentials.extra,
        }

    @classmethod
    async def _log_callback(
        cls,
        db: AsyncSession,
        platform: str,
        event_type: str,
        platform_order_id: str | None,
        order_id: int | None,
        raw: dict,
    ) -> None:
        db.add(
            DeliveryCallbackLog(
                platform=platform.upper(),
                event_type=event_type,
                platform_order_id=platform_order_id,
                order_id=order_id,
                payload=json.dumps(raw, ensure_ascii=False, default=str),
            )
        )
        await db.flush()

    @classmethod
    async def _add_item_options(
        cls,
        db: AsyncSession,
        order_item_id: int,
        option_ids: list[int],
        options_raw: list,
    ) -> None:
        for oid in option_ids:
            opt = await db.get(Option, oid)
            if not opt:
                continue
            group = await db.get(OptionGroup, opt.group_id)
            db.add(
                OrderItemOption(
                    order_item_id=order_item_id,
                    group_name_snapshot=group.name if group else "规格",
                    option_name_snapshot=opt.name,
                    option_code=opt.code,
                    extra_price_snapshot=opt.extra_price,
                )
            )
        if option_ids:
            return
        for opt in options_raw:
            if not isinstance(opt, dict):
                continue
            db.add(
                OrderItemOption(
                    order_item_id=order_item_id,
                    group_name_snapshot=str(opt.get("group") or opt.get("name") or "规格"),
                    option_name_snapshot=str(opt.get("value") or opt.get("option_name") or opt.get("name") or ""),
                    option_code=str(opt.get("code") or "EXT"),
                    extra_price_snapshot=money(opt.get("price") or 0),
                )
            )

    @classmethod
    async def _create_from_platform(
        cls,
        db,
        provider,
        parsed: dict,
        raw: dict,
        credentials: PlatformCredentials | None,
    ) -> tuple[Order, list[dict]]:
        store_id = parsed["store_id"]
        platform = parsed["platform"]
        items_data = parsed.get("items") or []
        if not items_data:
            raise AppException(40052, "外卖订单无商品明细")

        order_items_payload: list[dict] = []
        subtotal = Decimal("0.00")

        for it in items_data:
            product, option_ids = await cls._resolve_product(db, store_id, platform, it)
            unit = money(it["price"]) if it.get("price") else money(product.price)
            qty = int(it["quantity"])
            line = money(unit * qty)
            subtotal = money(subtotal + line)
            order_items_payload.append(
                {
                    "product": product,
                    "name": it.get("name") or product.name,
                    "unit_price": unit,
                    "quantity": qty,
                    "subtotal": line,
                    "option_ids": option_ids,
                    "options_raw": it.get("options") or [],
                }
            )

        packing = money(parsed.get("packing_fee") or 0)
        delivery_fee = money(parsed.get("delivery_fee") or 0)
        total = money(parsed.get("total_amount") or (subtotal + packing + delivery_fee))

        order = Order(
            order_no=generate_no("D"),
            store_id=store_id,
            status="PAID",
            payment_status="PAID",
            payment_method=platform,
            channel=platform,
            subtotal=subtotal,
            discount_amount=Decimal("0.00"),
            total_amount=total,
            remark=parsed.get("remark"),
            idempotency_key=f"{platform}:{parsed['platform_order_id']}",
            ordered_at=now_local(),
            paid_at=now_local(),
        )
        db.add(order)
        await db.flush()

        for row in order_items_payload:
            oi = OrderItem(
                order_id=order.id,
                product_id=row["product"].id,
                product_name_snapshot=row["name"],
                unit_price_snapshot=row["unit_price"],
                quantity=row["quantity"],
                subtotal=row["subtotal"],
            )
            db.add(oi)
            await db.flush()
            await cls._add_item_options(db, oi.id, row["option_ids"], row["options_raw"])

        d_order = DeliveryOrder(
            order_id=order.id,
            platform=platform,
            platform_order_id=parsed["platform_order_id"],
            platform_day_seq=parsed.get("platform_day_seq"),
            delivery_status="PENDING",
            recipient_name=parsed.get("recipient_name"),
            recipient_phone=parsed.get("recipient_phone"),
            recipient_address=parsed.get("recipient_address"),
            packing_fee=packing,
            delivery_fee=delivery_fee,
            raw_payload=json.dumps(raw, ensure_ascii=False, default=str),
            last_sync_at=now_local(),
        )
        db.add(d_order)
        await db.flush()

        # 乐观接单：本地先 CONFIRMED，平台 confirm 异步回传
        d_order.delivery_status = "ACCEPTED"
        order.status = "CONFIRMED"

        deferred = [
            {
                "action": "confirm_order",
                "platform": platform,
                "platform_order_id": parsed["platform_order_id"],
                "credentials": cls._creds_dict(credentials),
            }
        ]
        return order, deferred

    @classmethod
    async def _resolve_product(
        cls, db: AsyncSession, store_id: int, platform: str, item: dict
    ) -> tuple[Product, list[int]]:
        sku = item.get("platform_sku_id") or item.get("app_food_code") or ""
        app_code = item.get("app_food_code") or sku
        option_codes = [str(o.get("code") or o.get("value") or "") for o in (item.get("options") or []) if isinstance(o, dict)]
        sig = ProductSyncService.build_option_signature(option_codes)

        mapping = None
        if app_code or sku:
            q = select(DeliveryProductMap).where(
                DeliveryProductMap.store_id == store_id,
                DeliveryProductMap.platform == platform,
                DeliveryProductMap.status == 1,
            )
            if sig and sig != "DEFAULT":
                result = await db.execute(q.where(DeliveryProductMap.app_food_code == app_code, DeliveryProductMap.option_signature == sig))
                mapping = result.scalar_one_or_none()
            if not mapping and app_code:
                result = await db.execute(q.where(DeliveryProductMap.app_food_code == app_code))
                mapping = result.scalar_one_or_none()
            if not mapping and sku:
                result = await db.execute(q.where(DeliveryProductMap.platform_sku_id == sku))
                mapping = result.scalar_one_or_none()

        if mapping:
            product = await db.get(Product, mapping.product_id)
            if not product:
                raise AppException(40451, f"映射商品不存在: {mapping.product_id}")
            option_ids: list[int] = []
            if mapping.option_map_json:
                try:
                    option_ids = [int(v) for v in json.loads(mapping.option_map_json).values()]
                except Exception:
                    option_ids = []
            return product, option_ids

        name = item.get("name") or ""
        if name:
            result = await db.execute(select(Product).where(Product.name == name, Product.status == "ON_SALE"))
            product = result.scalar_one_or_none()
            if product:
                logger.warning("外卖商品未配置映射，按名称匹配: %s", name)
                return product, []

        result = await db.execute(select(Product).where(Product.status == "ON_SALE").limit(1))
        product = result.scalar_one_or_none()
        if not product:
            raise AppException(40452, f"无法解析外卖商品: {sku or name}")
        logger.warning("外卖商品无映射，使用兜底商品 product_id=%s sku=%s", product.id, sku)
        return product, []

    @classmethod
    async def _get_delivery_order(cls, db: AsyncSession, platform: str, platform_order_id: str) -> DeliveryOrder | None:
        result = await db.execute(
            select(DeliveryOrder).where(
                DeliveryOrder.platform == platform.upper(),
                DeliveryOrder.platform_order_id == platform_order_id,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def _handle_cancel(cls, db: AsyncSession, platform: str, platform_order_id: str, raw: dict | None = None) -> Order | None:
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None
        order = await db.get(Order, d_order.order_id)
        if order and order.status not in ("COMPLETED", "CANCELLED"):
            order.status = "CANCELLED"
            order.cancelled_at = now_local()
            d_order.delivery_status = "CANCELLED"
            d_order.last_sync_at = now_local()
            await db.flush()
        return order

    @classmethod
    async def _handle_completed(cls, db: AsyncSession, platform: str, platform_order_id: str) -> Order | None:
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None
        order = await db.get(Order, d_order.order_id)
        if order:
            order.status = "COMPLETED"
            order.completed_at = now_local()
            d_order.delivery_status = "DONE"
            d_order.logistics_status = "DELIVERED"
            d_order.completed_at = now_local()
            d_order.last_sync_at = now_local()
            await db.flush()
        return order

    @classmethod
    async def _handle_confirmed(cls, db: AsyncSession, platform: str, platform_order_id: str) -> Order | None:
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None
        order = await db.get(Order, d_order.order_id)
        if order and order.status == "PAID":
            order.status = "CONFIRMED"
            d_order.delivery_status = "ACCEPTED"
            d_order.last_sync_at = now_local()
            await db.flush()
        return order

    @classmethod
    async def _handle_urge(
        cls,
        db: AsyncSession,
        platform: str,
        platform_order_id: str,
        raw: dict,
        credentials: PlatformCredentials | None,
    ) -> tuple[Order | None, list[dict]]:
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None, []
        d_order.urge_count = (d_order.urge_count or 0) + 1
        d_order.last_sync_at = now_local()
        await db.flush()
        tasks = [
            {
                "action": "reply_urge",
                "platform": platform.upper(),
                "platform_order_id": platform_order_id,
                "raw": raw,
                "credentials": cls._creds_dict(credentials),
            }
        ]
        order = await db.get(Order, d_order.order_id)
        return order, tasks

    @classmethod
    async def _handle_delivery(cls, db: AsyncSession, platform: str, parsed: dict, raw: dict) -> Order | None:
        platform_order_id = parsed.get("platform_order_id") or ""
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None

        logistics_status = parsed.get("logistics_status") or raw.get("logistics_status") or raw.get("status")
        logistics_code = parsed.get("logistics_code") or raw.get("logistics_code") or raw.get("logisticsCode")
        rider_name = parsed.get("rider_name") or raw.get("rider_name") or raw.get("courier_name")
        rider_phone = parsed.get("rider_phone") or raw.get("rider_phone") or raw.get("courier_phone")

        d_order.logistics_status = str(logistics_status) if logistics_status else d_order.logistics_status
        d_order.logistics_code = str(logistics_code) if logistics_code else d_order.logistics_code
        if rider_name:
            d_order.rider_name = str(rider_name)
        if rider_phone:
            d_order.rider_phone = str(rider_phone)
        d_order.last_sync_at = now_local()

        order = await db.get(Order, d_order.order_id)
        if order and str(logistics_status).upper() in ("DELIVERING", "RIDER_TAKE", "PICKUP", "配送中"):
            if order.status in ("CONFIRMED", "PREPARING", "READY"):
                order.status = "PREPARING"
        await db.flush()
        return order

    @classmethod
    async def _handle_privacy(cls, db: AsyncSession, platform: str, parsed: dict, raw: dict) -> Order | None:
        platform_order_id = parsed.get("platform_order_id") or ""
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None
        phone = parsed.get("recipient_phone") or raw.get("recipient_phone") or raw.get("backup_phone")
        if phone:
            d_order.recipient_phone = str(phone)
            d_order.last_sync_at = now_local()
            await db.flush()
        return await db.get(Order, d_order.order_id)

    @classmethod
    async def _handle_refund(cls, db: AsyncSession, platform: str, parsed: dict, raw: dict) -> Order | None:
        platform_order_id = parsed.get("platform_order_id") or ""
        d_order = await cls._get_delivery_order(db, platform, platform_order_id)
        if not d_order:
            return None

        order = await db.get(Order, d_order.order_id)
        refund_id = str(raw.get("refund_id") or raw.get("refundId") or parsed.get("refund_id") or "")
        amount = money(parsed.get("refund_amount") or raw.get("refund_amount") or raw.get("refundAmount") or 0)
        reason = str(raw.get("reason") or raw.get("refund_reason") or parsed.get("reason") or "")
        refund_type = "PARTIAL" if amount and order and amount < order.total_amount else "FULL"

        existing = None
        if refund_id:
            result = await db.execute(
                select(DeliveryRefund).where(
                    DeliveryRefund.platform == platform.upper(),
                    DeliveryRefund.platform_refund_id == refund_id,
                )
            )
            existing = result.scalar_one_or_none()

        if not existing:
            db.add(
                DeliveryRefund(
                    delivery_order_id=d_order.id,
                    order_id=d_order.order_id,
                    platform=platform.upper(),
                    platform_refund_id=refund_id or None,
                    refund_type=refund_type,
                    amount=amount,
                    reason=reason,
                    status="PENDING",
                    raw_payload=json.dumps(raw, ensure_ascii=False, default=str),
                )
            )

        d_order.refund_status = "PENDING"
        d_order.last_sync_at = now_local()
        await db.flush()
        return order

    @classmethod
    async def process_refund(
        cls,
        db: AsyncSession,
        refund_id: int,
        approve: bool,
        reason: str | None = None,
    ) -> dict:
        refund = await db.get(DeliveryRefund, refund_id)
        if not refund:
            raise AppException(40453, "退款记录不存在")
        if refund.status != "PENDING":
            raise AppException(40053, f"退款已处理: {refund.status}")

        d_order = await db.get(DeliveryOrder, refund.delivery_order_id)
        order = await db.get(Order, refund.order_id) if refund.order_id else None
        credentials = await get_credentials_for_store(db, refund.platform, order.store_id if order else 1)
        provider = get_delivery_provider(refund.platform, credentials)

        if approve:
            ok = await provider.agree_refund(refund.platform_refund_id or "", d_order.platform_order_id if d_order else "")
            refund.status = "APPROVED"
            if d_order:
                d_order.refund_status = "APPROVED"
                order = await db.get(Order, d_order.order_id)
                if order and refund.refund_type == "FULL":
                    order.status = "CANCELLED"
                    order.cancelled_at = now_local()
                    d_order.delivery_status = "CANCELLED"
        else:
            ok = await provider.disagree_refund(
                refund.platform_refund_id or "",
                d_order.platform_order_id if d_order else "",
                reason or "商家拒绝退款",
            )
            refund.status = "REJECTED"
            if d_order:
                d_order.refund_status = "REJECTED"

        refund.handled_at = now_local()
        await db.flush()
        return {"ok": ok, "status": refund.status, "refund_type": refund.refund_type}

    @classmethod
    async def cancel_by_merchant(cls, db: AsyncSession, delivery_order_id: int, reason: str) -> dict:
        d_order = await db.get(DeliveryOrder, delivery_order_id)
        if not d_order:
            raise AppException(40454, "外卖订单不存在")
        order = await db.get(Order, d_order.order_id)
        if order and order.status in ("COMPLETED", "CANCELLED"):
            raise AppException(40054, "订单已结束，无法取消")

        credentials = await get_credentials_for_store(db, d_order.platform, order.store_id if order else 1)
        provider = get_delivery_provider(d_order.platform, credentials)
        ok = await provider.cancel_order(d_order.platform_order_id, reason)
        if ok and order:
            order.status = "CANCELLED"
            order.cancelled_at = now_local()
            d_order.delivery_status = "CANCELLED"
            d_order.last_sync_at = now_local()
            await db.flush()
        return {"ok": ok, "order_id": order.id if order else None}

    @classmethod
    async def sync_kitchen_status(cls, db: AsyncSession, order: Order) -> None:
        if order.channel not in ("MEITUAN", "ELEME"):
            return
        result = await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order.id))
        d_order = result.scalar_one_or_none()
        if not d_order:
            return
        sync_status = STATUS_SYNC_MAP.get(order.status)
        if not sync_status:
            return
        _, credentials = await resolve_store_and_credentials(db, d_order.platform, {}, {"store_id": order.store_id})
        if not credentials or not credentials.platform_shop_id:
            credentials = await get_credentials_for_store(db, d_order.platform, order.store_id)
        provider = get_delivery_provider(d_order.platform, credentials)
        await provider.sync_status(d_order.platform_order_id, sync_status)
        d_order.delivery_status = sync_status
        d_order.last_sync_at = now_local()
        if sync_status == "DONE":
            d_order.completed_at = now_local()
        await db.flush()
