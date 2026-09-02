"""厨房小票打印：芯烨云 XP-80T，含订单号 CODE128 条码。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import parse_biz_no
from app.core.config import get_settings
from app.core.response import now_local
from app.models import DeliveryOrder, DiningTable, Order, OrderItem, OrderItemOption, Product, Store, StoreSetting
from app.services.customer_display import pickup_code_from_order_no
from app.services.xpyun_client import XpyunClient

logger = logging.getLogger(__name__)

TICKET_KITCHEN = "KITCHEN"
TICKET_URGE = "URGE"
TICKET_CANCEL = "CANCEL"

_CHANNEL_LABEL = {
    "WECHAT": "微信堂食",
    "ALIPAY": "支付宝堂食",
    "MEITUAN": "美团外卖",
    "ELEME": "饿了么",
    "H5": "H5点餐",
}

_LINE = "--------------------------------"


@dataclass
class OrderPrintContext:
    order: Order
    store: Store | None
    table: DiningTable | None
    delivery: DeliveryOrder | None
    items: list[OrderItem]
    options_by_item: dict[int, list[OrderItemOption]]
    no_cooking_by_product: dict[int, bool]


def deferred_print_task(order_id: int, ticket_type: str = TICKET_KITCHEN) -> dict[str, Any]:
    return {"action": "print_ticket", "order_id": int(order_id), "ticket_type": ticket_type}


def _escape_print_text(text: str) -> str:
    return (text or "").replace("<", "&lt;").replace(">", "&gt;")


def _barcode_payload(order_no: str) -> str:
    """芯烨 CODE128 条码内容：订单号仅含字母数字，可直接编码。"""
    s = re.sub(r"[^A-Za-z0-9]", "", (order_no or "").strip().upper())
    return s or "0"


def _format_options(opts: list[OrderItemOption]) -> str:
    if not opts:
        return ""
    parts = []
    for o in opts:
        name = (o.option_name_snapshot or o.option_code or "").strip()
        if not name:
            continue
        qty = int(o.quantity or 1)
        if qty > 1:
            parts.append(f"{name}x{qty}")
        else:
            parts.append(name)
    return "、".join(parts)


def _channel_label(order: Order) -> str:
    ch = (order.channel or order.payment_method or "").upper()
    return _CHANNEL_LABEL.get(ch, ch or "门店")


def _header_line(ctx: OrderPrintContext, ticket_type: str) -> str:
    if ticket_type == TICKET_URGE:
        return "<C><B>【催单】</B></C>"
    if ticket_type == TICKET_CANCEL:
        return "<C><B>【作废/取消】</B></C>"
    return "<C><B>厨房制作单</B></C>"


def _location_line(ctx: OrderPrintContext) -> str:
    order = ctx.order
    if ctx.delivery:
        seq = (ctx.delivery.platform_day_seq or "").strip()
        platform = _channel_label(order)
        head = f"{platform}"
        if seq:
            head += f" #{seq}"
        return head
    if ctx.table and (order.dining_type or "").upper() != "TAKEAWAY":
        label = (ctx.table.table_name or ctx.table.table_no or "").strip()
        return f"堂食 {label}"
    if (order.dining_type or "").upper() == "TAKEAWAY":
        code = pickup_code_from_order_no(order.order_no)
        return f"外带 取餐码 {code}"
    parsed = parse_biz_no(order.order_no or "")
    if parsed and parsed.get("table") not in ("000", "DLV", "TKW"):
        return f"桌位 {parsed['table']}"
    return _channel_label(order)


def build_ticket_content(ctx: OrderPrintContext, ticket_type: str = TICKET_KITCHEN) -> str:
    order = ctx.order
    store_name = (ctx.store.name if ctx.store else "").strip() or "SmartNoodleHouse"
    lines: list[str] = [
        _header_line(ctx, ticket_type),
        f"<C>{_escape_print_text(store_name)}</C>",
        f"<C>{_escape_print_text(_location_line(ctx))}</C>",
        f"时间 {now_local().strftime('%m-%d %H:%M')}",
        _LINE,
    ]

    if ticket_type == TICKET_KITCHEN:
        kitchen_items = [
            item
            for item in ctx.items
            if not ctx.no_cooking_by_product.get(int(item.product_id or 0), False)
        ]
        if not kitchen_items:
            kitchen_items = ctx.items
        for item in kitchen_items:
            name = _escape_print_text(item.product_name_snapshot or "商品")
            qty = int(item.quantity or 1)
            lines.append(f"<B>{name}</B>")
            lines.append(f"<C><B2>x{qty}</B2></C>")
            opts = _format_options(ctx.options_by_item.get(item.id, []))
            if opts:
                lines.append(f"  {_escape_print_text(opts)}")
            item_remark = (item.remark or "").strip()
            if item_remark:
                lines.append(f"  注:{_escape_print_text(item_remark)}")
        lines.append(_LINE)
        if ctx.delivery:
            addr = (ctx.delivery.recipient_address or "").strip()
            phone = (ctx.delivery.recipient_phone or "").strip()
            name = (ctx.delivery.recipient_name or "").strip()
            if name or phone:
                lines.append(f"顾客 {_escape_print_text(name)} {_escape_print_text(phone)}")
            if addr:
                lines.append(_escape_print_text(addr[:96]))
            lines.append(_LINE)
    else:
        lines.append(f"单号 {_escape_print_text(order.order_no)}")
        lines.append(_LINE)

    remark = (order.remark or "").strip()
    if remark and ticket_type == TICKET_KITCHEN:
        lines.append(f"备注 {_escape_print_text(remark[:200])}")
        lines.append(_LINE)

    order_no = (order.order_no or "").strip().upper()
    barcode = _barcode_payload(order_no)
    lines.extend(
        [
            f"<C><BOLD>{_escape_print_text(order_no)}</BOLD></C>",
            f'<C><BARCODE t="CODE128" w="2" h="72" p="2">{barcode}</BARCODE></C>',
            "<BR><CUT>",
        ]
    )
    return "<BR>".join(lines)


class PrintService:
    @classmethod
    async def resolve_printer_sn(cls, db: AsyncSession, store_id: int | None) -> str:
        settings = get_settings()
        if store_id:
            row = (
                await db.execute(
                    select(StoreSetting.setting_value).where(
                        StoreSetting.store_id == int(store_id),
                        StoreSetting.setting_key == "xpyun_printer_sn",
                    )
                )
            ).scalar_one_or_none()
            if row and str(row).strip():
                return str(row).strip()
        return (settings.xpyun_printer_sn or "").strip()

    @classmethod
    async def load_context(cls, db: AsyncSession, order_id: int) -> OrderPrintContext | None:
        order = await db.get(Order, order_id)
        if not order:
            return None
        items = list(
            (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id))).scalars().all()
        )
        item_ids = [i.id for i in items]
        opts_by_item: dict[int, list[OrderItemOption]] = {}
        if item_ids:
            for opt in (
                await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
            ).scalars().all():
                opts_by_item.setdefault(opt.order_item_id, []).append(opt)
        product_ids = list({int(i.product_id) for i in items if i.product_id})
        no_cooking_by_product: dict[int, bool] = {}
        if product_ids:
            for pid, nc in (
                await db.execute(select(Product.id, Product.no_cooking).where(Product.id.in_(product_ids)))
            ).all():
                no_cooking_by_product[int(pid)] = bool(nc)
        store = await db.get(Store, order.store_id) if order.store_id else None
        table = await db.get(DiningTable, order.table_id) if order.table_id else None
        delivery = (
            await db.execute(select(DeliveryOrder).where(DeliveryOrder.order_id == order_id))
        ).scalar_one_or_none()
        return OrderPrintContext(
            order=order,
            store=store,
            table=table,
            delivery=delivery,
            items=items,
            options_by_item=opts_by_item,
            no_cooking_by_product=no_cooking_by_product,
        )

    @classmethod
    async def print_order(
        cls,
        db: AsyncSession,
        order_id: int,
        *,
        ticket_type: str = TICKET_KITCHEN,
    ) -> dict[str, Any] | None:
        settings = get_settings()
        if not settings.xpyun_enabled:
            logger.debug("打印已关闭 xpyun_enabled=false order_id=%s", order_id)
            return None

        ctx = await cls.load_context(db, order_id)
        if not ctx:
            logger.warning("打印跳过：订单不存在 order_id=%s", order_id)
            return None

        if ticket_type == TICKET_KITCHEN and ctx.order.status in ("CANCELLED", "REFUNDED"):
            logger.info("打印跳过：订单已取消 order_id=%s", order_id)
            return None

        sn = await cls.resolve_printer_sn(db, ctx.order.store_id)
        content = build_ticket_content(ctx, ticket_type=ticket_type)

        if not sn or not get_settings().xpyun_user or not get_settings().xpyun_user_key:
            if settings.allow_print_mock:
                logger.warning(
                    "MOCK 打印 ticket=%s order=%s sn=%s\n%s",
                    ticket_type,
                    ctx.order.order_no,
                    sn or "(未配置)",
                    content.replace("<BR>", "\n"),
                )
                return {"mock": True, "order_no": ctx.order.order_no}
            logger.error("芯烨云未配置完整，跳过打印 order=%s", ctx.order.order_no)
            return None

        client = XpyunClient()
        data = await client.print_ticket(
            sn,
            content,
            copies=settings.xpyun_print_copies,
            cutter=1,
            voice=0 if ticket_type != TICKET_KITCHEN else settings.xpyun_voice,
            mode=1,
        )
        logger.info("厨房小票已发送 order=%s sn=%s type=%s", ctx.order.order_no, sn, ticket_type)
        return data
