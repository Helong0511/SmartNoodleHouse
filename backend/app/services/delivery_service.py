"""外卖订单接入服务：推单落库 → 统一 Order → 厨房；取消/退款/催单/配送"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.biz_no import BIZ_OD, TABLE_DELIVERY, generate_no
from app.core.exceptions import AppException
from app.core.response import dumps_json, money, now_local
from app.models import (
    DeliveryCallbackLog,
    DeliveryOrder,
    DeliveryProductMap,
    DeliveryRefund,
    DeliverySyncAlert,
    Option,
    OptionGroup,
    Order,
    OrderItem,
    OrderItemOption,
    Product,
    Store,
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
    "COOKING": "ACCEPTED",
    "READY": "READY",
    "COMPLETED": "DONE",
    "CANCELLED": "CANCELLED",
    "REFUNDED": "CANCELLED",
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

        return await cls._dispatch_event(db, provider, platform, event, parsed, raw, credentials, platform_order_id)

    @classmethod
    async def process_verified(
        cls,
        db: AsyncSession,
        platform: str,
        headers: dict,
        body: bytes,
    ) -> DeliveryNotifyResult:
        """兼容旧调用：解析 body 后走 process_raw。"""
        text = body.decode("utf-8")
        raw = json.loads(text) if text.strip().startswith("{") else {}
        if isinstance(raw, dict) and raw.get("_verified") and isinstance(raw.get("raw"), dict):
            raw = raw["raw"]
        return await cls.process_raw(db, platform, raw)

    @classmethod
    async def process_raw(cls, db: AsyncSession, platform: str, raw: dict) -> DeliveryNotifyResult:
        """已验签的 raw 业务处理（Inbox 消费入口）。"""
        event = classify_meituan_event(raw) if platform.upper() == "MEITUAN" else classify_eleme_event(raw)
        provider = get_delivery_provider(platform)
        parsed = provider.parse_order(raw)
        parsed["event"] = event
        platform_order_id = parsed.get("platform_order_id") or ""
        store_id, credentials = await resolve_store_and_credentials(db, platform, raw, parsed)
        parsed["store_id"] = store_id
        provider = get_delivery_provider(platform, credentials)
        await cls._log_callback(db, platform, event, platform_order_id, None, raw)
        return await cls._dispatch_event(db, provider, platform, event, parsed, raw, credentials, platform_order_id)

    @classmethod
    async def _dispatch_event(
        cls,
        db,
        provider,
        platform: str,
        event: str,
        parsed: dict,
        raw: dict,
        credentials,
        platform_order_id: str,
    ) -> DeliveryNotifyResult:
        if event == DeliveryEvent.ORDER_CANCEL:
            order = await cls._handle_cancel(db, platform, platform_order_id, raw)
            deferred = []
            if order:
                from app.services.print_service import deferred_print_task, TICKET_CANCEL

                deferred.append(deferred_print_task(order.id, TICKET_CANCEL))
            return DeliveryNotifyResult(event=event, order=order, deferred_tasks=deferred)

        if event == DeliveryEvent.REFUND:
            order = await cls._handle_refund(db, platform, parsed, raw)
            return DeliveryNotifyResult(event=event, order=order, extra={"refund_pending": True})

        if event == DeliveryEvent.URGE:
            order, tasks = await cls._handle_urge(db, platform, platform_order_id, raw, credentials)
            if order:
                from app.services.print_service import deferred_print_task, TICKET_URGE

                tasks = list(tasks) + [deferred_print_task(order.id, TICKET_URGE)]
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

        # 仅明确识别为新订单的事件允许创建本地订单。
        # UNKNOWN 是开放平台新增/未知回调码，必须记录并忽略，不能按新订单落库。
        if event != DeliveryEvent.ORDER_NEW:
            logger.info("忽略未识别或不支持的外卖事件 %s: %s", event, platform_order_id)
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
        if order is None:
            # 库存不足已拒单（无本地单），正常结束以 ack 平台消息，避免 Inbox 无限重试
            return DeliveryNotifyResult(
                event=DeliveryEvent.ORDER_NEW,
                order=None,
                deferred_tasks=tasks or [],
                extra={"rejected": True},
            )
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
        # 有平台事件 ID 时优先使用；限制长度以匹配数据库列宽。
        event_id = None
        for key in ("requestId", "request_id", "msg_id", "msgid", "notify_id", "event_id"):
            if raw.get(key):
                event_id = str(raw[key]).strip()[:128] or None
                break

        # 部分平台通知没有独立事件 ID。不能仅用 event_type + order_id：
        # 同一订单可能多次产生相同类型的合法状态通知，而日志表对此有唯一约束。
        # 对规范化后的完整事件内容做摘要：完全相同的回调稳定去重，不同内容不会撞同一个业务键。
        payload = dumps_json(raw)
        if not event_id:
            digest = hashlib.sha256(
                f"{platform.upper()}|{event_type}|{platform_order_id or ''}|{payload}".encode("utf-8")
            ).hexdigest()
            event_id = f"sha256:{digest}"
        # 截断 payload，避免无限期保存大量 PII
        db.add(
            DeliveryCallbackLog(
                platform=platform.upper(),
                event_type=event_type,
                event_id=event_id,
                platform_order_id=platform_order_id,
                order_id=order_id,
                payload=payload,
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
            raise AppException(40052, "外卖订单无餐品明细")

        order_items_payload: list[dict] = []
        subtotal = Decimal("0.00")

        for it in items_data:
            product, option_ids = await cls._resolve_product(db, store_id, platform, it)
            # 金额：以本系统餐品价为准，拒绝纯信平台价造成账实不符
            unit = money(product.price)
            if it.get("price") is not None and money(it["price"]) != unit:
                logger.warning(
                    "外卖单价与本地不一致 platform=%s product=%s platform_price=%s local=%s",
                    platform,
                    product.id,
                    it.get("price"),
                    unit,
                )
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
        local_total = money(subtotal + packing + delivery_fee)
        platform_total = money(parsed.get("total_amount") or 0)
        # 差异过大则拒绝（防篡改）；允许分位误差
        if platform_total > 0 and abs(platform_total - local_total) > Decimal("0.50"):
            raise AppException(
                40053,
                f"外卖金额与本地计算不一致 platform={platform_total} local={local_total}",
            )
        total = local_total

        # 原子扣库存（与堂食一致）。库存不足=跨平台超卖：主动取消平台订单 + 告警，不落本地单。
        from app.services.stock_service import deduct_for_order_items

        try:
            # SAVEPOINT：扣库失败回滚嵌套事务，释放库存行锁后再写告警/投递取消任务
            async with db.begin_nested():
                await deduct_for_order_items(
                    db,
                    [
                        {"product_id": row["product"].id, "quantity": row["quantity"]}
                        for row in order_items_payload
                    ],
                    store_id=store_id,
                )
        except AppException as e:
            if getattr(e, "code", None) != 40031:
                raise
            # 平台取消走 deferred，避免持库存锁或长事务等外网
            deferred = cls._reject_order_soldout_tasks(
                parsed, credentials, reason=str(e)
            )
            await cls._write_reject_alert(db, parsed, str(e))
            return None, deferred

        store = await db.get(Store, int(store_id)) if store_id else None
        order = Order(
            order_no=generate_no(
                BIZ_OD, store.store_no if store else None, None, table_placeholder=TABLE_DELIVERY
            ),
            store_id=store_id,
            status="PAID",
            payment_status="PAID",
            payment_method=platform,
            channel=platform,
            dining_type="TAKEAWAY",
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
            raw_payload=dumps_json(raw),
            last_sync_at=now_local(),
        )
        db.add(d_order)
        await db.flush()

        from app.services.financial_ledger_service import FinancialLedgerService
        from app.services.status_history import record_order_status

        await record_order_status(
            db, order_id=order.id, from_status=None, to_status="PAID", source="DELIVERY", remark="外卖落单"
        )
        try:
            await FinancialLedgerService.record_sale(
                db,
                store_id=int(store_id),
                amount=total,
                channel=platform,
                provider=platform,
                order_id=order.id,
                payment_id=None,
                delivery_order_id=d_order.id,
                remark=f"delivery:{platform}:{parsed['platform_order_id']}",
            )
        except Exception as exc:
            logger.exception(
                "外卖 SALE 入账失败 order_id=%s platform=%s", order.id, platform
            )
            if get_settings().is_production:
                raise AppException(50010, "外卖财务分录写入失败，请重试回调") from exc

        from app.services.system_settings_service import SystemSettingsService

        deferred: list[dict] = []
        auto_confirm = await SystemSettingsService.effective_bool(
            db,
            "delivery_auto_confirm",
            get_settings().delivery_auto_confirm,
            store_id=int(store_id),
        )
        if auto_confirm:
            # 本地先标记 ACCEPTED，平台 confirm 失败由 deferred 回滚任务处理
            from app.services.order_lifecycle import apply_order_status

            d_order.delivery_status = "ACCEPTED"
            await apply_order_status(
                db, order, "CONFIRMED", source="DELIVERY", assert_kind="SYSTEM", remark="自动接单"
            )
            deferred.append(
                {
                    "action": "confirm_order",
                    "platform": platform,
                    "platform_order_id": parsed["platform_order_id"],
                    "credentials": cls._creds_dict(credentials),
                    "order_id": order.id,
                    "rollback_on_fail": True,
                }
            )
        else:
            # 默认：仅落库 PAID/PENDING，等待商家手动接单再调平台
            d_order.delivery_status = "PENDING"

        return order, deferred

    @classmethod
    async def merchant_confirm(cls, db: AsyncSession, order_id: int) -> tuple[Order, list[dict]]:
        """商家手动接单：本地 CONFIRMED + 异步平台 confirm。"""
        order = await db.get(Order, order_id)
        if not order:
            raise AppException(40404, "订单不存在")
        d_order = (
            await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order_id))
        ).scalar_one_or_none()
        if not d_order:
            raise AppException(40450, "非外卖订单")
        if d_order.delivery_status not in ("PENDING", "ACCEPTED"):
            raise AppException(40054, f"当前配送状态不可接单: {d_order.delivery_status}")
        credentials = await get_credentials_for_store(db, d_order.platform, order.store_id)
        d_order.delivery_status = "ACCEPTED"
        from app.services.order_lifecycle import apply_order_status

        await apply_order_status(
            db, order, "CONFIRMED", source="DELIVERY", assert_kind="SYSTEM", remark="商家接单"
        )
        deferred = [
            {
                "action": "confirm_order",
                "platform": d_order.platform,
                "platform_order_id": d_order.platform_order_id,
                "credentials": cls._creds_dict(credentials),
                "order_id": order.id,
                "rollback_on_fail": True,
            }
        ]
        return order, deferred

    @classmethod
    def _reject_order_soldout_tasks(
        cls,
        parsed: dict,
        credentials: PlatformCredentials | None,
        *,
        reason: str,
    ) -> list[dict]:
        """库存不足拒单：平台取消放入 deferred，由 run_deferred_tasks 锁外执行。"""
        platform_order_id = str(parsed.get("platform_order_id") or "")
        if not platform_order_id:
            return []
        return [
            {
                "action": "cancel_order",
                "platform": str(parsed.get("platform") or "").upper(),
                "platform_order_id": platform_order_id,
                "credentials": cls._creds_dict(credentials) if credentials else {},
                "reason": "餐品售罄，商家无法接单",
                "detail": reason[:500] if reason else None,
            }
        ]

    @classmethod
    async def _write_reject_alert(cls, db: AsyncSession, parsed: dict, reason: str) -> None:
        platform_order_id = str(parsed.get("platform_order_id") or "")
        db.add(
            DeliverySyncAlert(
                store_id=parsed.get("store_id"),
                platform=parsed.get("platform"),
                alert_type="REJECT_ORDER",
                title=f"库存不足已拒单 {platform_order_id}".strip(),
                detail=reason,
                platform_order_id=platform_order_id or None,
            )
        )
        await db.flush()

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
                raise AppException(40451, f"映射餐品不存在: {mapping.product_id}")
            if int(product.store_id) != int(store_id):
                raise AppException(40301, f"外卖餐品映射跨门店，拒绝落单: {mapping.id}")
            option_ids: list[int] = []
            if mapping.option_map_json:
                # SQLAlchemy JSON 列在正常读取时已反序列化为 dict；
                # 兼容历史 TEXT 字符串，但映射损坏时必须拒绝落单，不能静默丢失规格。
                try:
                    option_map = mapping.option_map_json
                    if isinstance(option_map, str):
                        option_map = json.loads(option_map)
                    if not isinstance(option_map, dict):
                        raise ValueError("option_map_json 必须是对象")
                    option_ids = [int(v) for v in option_map.values()]
                    if any(option_id <= 0 for option_id in option_ids):
                        raise ValueError("规格选项 ID 必须为正整数")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    logger.exception("外卖规格映射数据无效 map_id=%s", mapping.id)
                    raise AppException(40952, "外卖规格映射配置无效，请联系门店管理员修复") from exc
            return product, option_ids

        name = item.get("name") or ""
        raise AppException(
            40452,
            f"外卖餐品未配置映射，拒绝落单: sku={sku or '-'} name={name or '-'}",
        )

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
        if order and order.status not in ("COMPLETED", "CANCELLED", "REFUNDED"):
            from app.services.order_lifecycle import apply_order_status

            await apply_order_status(
                db,
                order,
                "CANCELLED",
                source="DELIVERY",
                assert_kind="SYSTEM",
                remark="平台取消",
            )
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
        if order and order.status not in ("COMPLETED", "CANCELLED", "REFUNDED"):
            from app.services.order_lifecycle import apply_order_status

            await apply_order_status(
                db,
                order,
                "COMPLETED",
                source="DELIVERY",
                assert_kind="SYSTEM",
                remark="平台完成",
            )
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
            from app.services.order_lifecycle import apply_order_status

            await apply_order_status(
                db,
                order,
                "CONFIRMED",
                source="DELIVERY",
                assert_kind="SYSTEM",
                remark="平台已确认",
            )
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
        # 原子自增，避免并发催单事件基于旧快照互相覆盖计数（丢失更新）
        from sqlalchemy import update as sa_update

        await db.execute(
            sa_update(DeliveryOrder)
            .where(DeliveryOrder.id == d_order.id)
            .values(
                urge_count=DeliveryOrder.urge_count + 1,
                last_sync_at=now_local(),
            )
        )
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
        # 仅向前推进：配送中 → 制作中；勿把 READY/COOKING 倒退
        if order and str(logistics_status).upper() in ("DELIVERING", "RIDER_TAKE", "PICKUP", "配送中"):
            if order.status in ("PAID", "CONFIRMED"):
                from app.services.order_lifecycle import apply_order_status

                await apply_order_status(
                    db,
                    order,
                    "PREPARING",
                    source="DELIVERY",
                    assert_kind="SYSTEM",
                    remark=f"物流={logistics_status}",
                )
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

        if not refund_id:
            # 平台未给 refund_id：不受唯一约束保护，直接插入
            db.add(
                DeliveryRefund(
                    delivery_order_id=d_order.id,
                    order_id=d_order.order_id,
                    platform=platform.upper(),
                    platform_refund_id=None,
                    refund_type=refund_type,
                    amount=amount,
                    reason=reason,
                    status="PENDING",
                    raw_payload=dumps_json(raw),
                )
            )
        else:
            # 有 refund_id：利用唯一约束原子去重，IntegrityError 时回滚 savepoint 再查已有行
            from sqlalchemy.exc import IntegrityError as _IntegrityError

            try:
                async with db.begin_nested():
                    db.add(
                        DeliveryRefund(
                            delivery_order_id=d_order.id,
                            order_id=d_order.order_id,
                            platform=platform.upper(),
                            platform_refund_id=refund_id,
                            refund_type=refund_type,
                            amount=amount,
                            reason=reason,
                            status="PENDING",
                            raw_payload=dumps_json(raw),
                        )
                    )
            except _IntegrityError:
                # 并发回调：(platform, platform_refund_id) 冲突 → 已有台账，跳过
                pass

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
        """处理外卖退款：短锁认领 PROCESSING → 锁外调平台 → 短锁落终态。

        避免在 DeliveryRefund 行锁内等待美团/饿了么 HTTP。
        """
        refund = (
            await db.execute(
                select(DeliveryRefund).where(DeliveryRefund.id == int(refund_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not refund:
            raise AppException(40453, "退款记录不存在")
        if refund.status in ("APPROVED", "REJECTED"):
            raise AppException(40053, f"退款已处理: {refund.status}")
        if refund.status == "PROCESSING":
            raise AppException(40953, "退款正在平台处理中，请勿重复提交；请先核对平台退款结果")
        if refund.status != "PENDING":
            raise AppException(40053, f"退款当前状态不可处理: {refund.status}")

        d_order = await db.get(DeliveryOrder, refund.delivery_order_id)
        order = await db.get(Order, refund.order_id) if refund.order_id else None
        if not order:
            raise AppException(40454, "关联订单不存在，无法处理外卖退款")
        store_id = int(order.store_id)
        credentials = await get_credentials_for_store(db, refund.platform, store_id)
        if not credentials:
            raise AppException(40061, f"门店 {store_id} 未配置 {refund.platform} 外卖凭证")

        platform = refund.platform
        platform_refund_id = refund.platform_refund_id or ""
        platform_order_id = d_order.platform_order_id if d_order else ""
        refund_type = refund.refund_type
        refund_amount = refund.amount
        delivery_order_id = refund.delivery_order_id
        order_channel = getattr(order, "channel", None)

        # 认领后立刻释放行锁，再调平台
        if refund.status == "PENDING":
            refund.status = "PROCESSING"
            await db.flush()
        await db.commit()

        provider = get_delivery_provider(platform, credentials)
        try:
            if approve:
                ok = await provider.agree_refund(platform_refund_id, platform_order_id)
            else:
                ok = await provider.disagree_refund(
                    platform_refund_id,
                    platform_order_id,
                    reason or "商家拒绝退款",
                )
        except Exception as exc:
            # 网络超时/连接中断时平台可能已受理；保持 PROCESSING，禁止盲目重试造成重复退款。
            logger.exception("外卖退款平台结果不确定，保留 PROCESSING refund_id=%s", refund_id)
            raise AppException(50353, "平台退款结果暂不确定，退款单已锁定处理中，请先查询平台结果") from exc

        refund = (
            await db.execute(
                select(DeliveryRefund).where(DeliveryRefund.id == int(refund_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not refund:
            raise AppException(40453, "退款记录不存在")
        if refund.status in ("APPROVED", "REJECTED"):
            return {"ok": True, "status": refund.status, "refund_type": refund.refund_type}
        if refund.status != "PROCESSING":
            raise AppException(40053, f"退款已处理: {refund.status}")

        if not ok:
            # 平台拒绝：回 PENDING 供商家重试
            refund.status = "PENDING"
            await db.flush()
            await db.commit()
            raise AppException(40062, "平台拒绝同意退款，请稍后重试或联系平台" if approve else "平台拒绝退款拒绝操作，请稍后重试或联系平台")

        d_order = await db.get(DeliveryOrder, delivery_order_id)
        order = await db.get(Order, refund.order_id) if refund.order_id else None

        if approve:
            refund.status = "APPROVED"
            if d_order:
                d_order.refund_status = "APPROVED"
                order = await db.get(Order, d_order.order_id)
                if order and refund_type == "FULL" and order.status not in ("CANCELLED", "REFUNDED"):
                    from app.services.order_lifecycle import apply_order_status

                    await apply_order_status(
                        db,
                        order,
                        "REFUNDED",
                        source="DELIVERY",
                        payment_status="REFUNDED",
                        assert_kind="SYSTEM",
                        remark="外卖全额退款同意",
                    )
                    d_order.delivery_status = "CANCELLED"
            if order:
                try:
                    from app.services.financial_ledger_service import FinancialLedgerService

                    await FinancialLedgerService.record_refund(
                        db,
                        store_id=int(order.store_id),
                        amount=refund_amount,
                        channel=platform or order_channel,
                        provider=platform,
                        order_id=order.id,
                        delivery_order_id=delivery_order_id,
                        delivery_refund_id=refund.id,
                        remark=f"delivery_refund:{platform}:{platform_refund_id or refund.id}",
                    )
                except Exception as exc:
                    logger.exception(
                        "外卖 REFUND 入账失败 delivery_refund_id=%s order_id=%s",
                        refund.id,
                        order.id,
                    )
                    if get_settings().is_production:
                        raise AppException(50010, "外卖退款分录写入失败，请重试") from exc
        else:
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
        if not order:
            raise AppException(40454, "关联订单不存在，无法取消外卖单")
        if order.status in ("COMPLETED", "CANCELLED", "REFUNDED"):
            raise AppException(40054, "订单已结束，无法取消")

        store_id = int(order.store_id)
        platform = d_order.platform
        platform_order_id = d_order.platform_order_id
        order_id = int(order.id)
        credentials = await get_credentials_for_store(db, platform, store_id)
        if not credentials:
            raise AppException(40061, f"门店 {store_id} 未配置 {platform} 外卖凭证")
        # 平台取消 HTTP 与本地落库分离，避免长事务拖住连接
        await db.commit()
        provider = get_delivery_provider(platform, credentials)
        ok = await provider.cancel_order(platform_order_id, reason)
        if ok:
            from app.services.order_lifecycle import apply_order_status

            order = await db.get(Order, order_id)
            d_order = await db.get(DeliveryOrder, delivery_order_id)
            if order and order.status not in ("COMPLETED", "CANCELLED", "REFUNDED"):
                await apply_order_status(
                    db,
                    order,
                    "CANCELLED",
                    source="DELIVERY",
                    assert_kind="SYSTEM",
                    remark=reason or "商家取消",
                )
            if d_order:
                d_order.delivery_status = "CANCELLED"
                d_order.last_sync_at = now_local()
            await db.flush()
        return {"ok": ok, "order_id": order_id}

    @classmethod
    async def sync_kitchen_status(cls, db: AsyncSession, order: Order) -> None:
        """厨房状态同步到外卖平台。调用方应已 commit 释放 Order 行锁。"""
        if order.channel not in ("MEITUAN", "ELEME"):
            return
        result = await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order.id))
        d_order = result.scalar_one_or_none()
        if not d_order:
            return
        sync_status = STATUS_SYNC_MAP.get(order.status)
        if not sync_status:
            return
        platform = d_order.platform
        platform_order_id = d_order.platform_order_id
        store_id = order.store_id
        delivery_order_id = int(d_order.id)
        _, credentials = await resolve_store_and_credentials(
            db, platform, {}, {"store_id": store_id}
        )
        if not credentials or not credentials.platform_shop_id:
            credentials = await get_credentials_for_store(db, platform, store_id)
        # 平台 HTTP：不持有订单/外卖扩展单行锁
        provider = get_delivery_provider(platform, credentials)
        await provider.sync_status(platform_order_id, sync_status)

        d_order = await db.get(DeliveryOrder, delivery_order_id)
        if not d_order:
            return
        d_order.delivery_status = sync_status
        d_order.last_sync_at = now_local()
        if sync_status == "DONE":
            d_order.completed_at = now_local()
        await db.flush()
