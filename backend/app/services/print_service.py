"""厨房小票打印服务。

职责概览
--------
1. 把订单拼成带标签的小票文本（build_ticket_content）
2. 按配置走局域网 ESC/POS（芯烨 XP-80T WiFi）或芯烨云 API（print_order）

相关配置（.env）
----------------
- XPYUN_ENABLED          总开关，false 则完全不打印
- PRINTER_MODE           local=局域网 | xpyun=芯烨云
- PRINTER_HOST / PORT    局域网打印机 IP 与端口（默认 9100）
- KITCHEN_TICKET_ORDER_NO  票底订单号：barcode | text | off
- ALLOW_PRINT_MOCK       未配打印机时是否只打日志模拟

标签语法（本文件生成，由 escpos_client / 芯烨云解析）
--------------------------------------------------
- <BR>     换行
- <C>…</C> 居中
- <B>…</B> 加粗
- <B2>…</B2> 倍高倍宽（加大字）
- <BARCODE …>内容</BARCODE>  CODE-128 条码
- <CUT>    切纸

改票头样式：看本文件「票头排版」一节的 _header_line / _meta_line / _dining_highlight。
改菜品行：看 _kitchen_item_name_qty_line。
改条码：看 _order_no_footer_lines，以及 escpos_client.py 的 CODE-128 实现。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.biz_no import parse_biz_no
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppException
from app.core.error_codes import PRINTER_UNREACHABLE
from app.core.response import now_local
from app.models import (
    DeliveryOrder,
    DiningTable,
    Order,
    OrderItem,
    OrderItemOption,
    PrintJob,
    Product,
    Store,
    StoreSetting,
)
from app.services.customer_display import pickup_code_from_order_no
from app.services.escpos_client import EscposClient
from app.services.xpyun_client import XpyunClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 小票类型常量（传给 build_ticket_content / print_order 的 ticket_type）
# ---------------------------------------------------------------------------
TICKET_KITCHEN = "KITCHEN"  # 正常厨房制作单
TICKET_URGE = "URGE"  # 催单（票头加【催单】）
TICKET_CANCEL = "CANCEL"  # 作废/取消（票头加【作废】）

# PrintJob 重试：最多 N 次；指数退避 2^attempt 秒，封顶 1 小时
MAX_PRINT_ATTEMPTS = 8
PRINT_BACKOFF_BASE_SEC = 2
PRINT_BACKOFF_CAP_SEC = 3600
# PRINTING 超时回收：locked_at 早于 now - STALE_PRINTING_SEC 视为崩溃残留，复位 PENDING
STALE_PRINTING_SEC = 300
# TEXT 可截断上限（留余量）
PRINT_CONTENT_MAX_LEN = 60000

# 控制字符（含 ESC 0x1b / GS 0x1d），写入 content 前必须清除
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 渠道英文码 → 小票上显示的中文名（外卖行、兜底渠道文案会用到）
_CHANNEL_LABEL = {
    "WECHAT": "微信堂食",
    "ALIPAY": "支付宝堂食",
    "MEITUAN": "美团外卖",
    "ELEME": "饿了么",
    "H5": "H5点餐",
}

# 分隔线：80mm 正常字号约 48 半角列，用 48 个 '-' 铺满一行
_LINE = "-" * 48


@dataclass
class OrderPrintContext:
    """打印所需的订单快照（一次查库装齐，避免拼票时再打散查）。

    attributes:
        order: 订单主表
        store: 门店（取店名印票头）
        table: 桌台（堂食桌名）
        delivery: 外卖单（有则按外卖排版）
        items: 订单商品行
        options_by_item: 商品行 id → 口味/加料选项列表
        no_cooking_by_product: 商品 id → 是否「无需制作」（True 则制作单可跳过该菜）
    """

    order: Order
    store: Store | None
    table: DiningTable | None
    delivery: DeliveryOrder | None
    items: list[OrderItem]
    options_by_item: dict[int, list[OrderItemOption]]
    no_cooking_by_product: dict[int, bool]


def deferred_print_task(order_id: int, ticket_type: str = TICKET_KITCHEN) -> dict[str, Any]:
    """生成「延后打印」任务字典，供队列 / 调度器消费（不是立刻出纸）。"""
    return {"action": "print_ticket", "order_id": int(order_id), "ticket_type": ticket_type}


def _print_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _print_backoff_seconds(attempt_count: int) -> int:
    """指数退避：2^attempt 秒，封顶 1 小时。"""
    exp = max(0, int(attempt_count))
    return min(PRINT_BACKOFF_CAP_SEC, PRINT_BACKOFF_BASE_SEC**exp)


def _strip_print_controls(text: str) -> str:
    """清除 ESC/GS/控制字符（不含标签转义，供整票 content 落库/发送前使用）。"""
    return _CTRL_CHARS_RE.sub("", text or "")


def _prepare_job_content(content: str) -> str:
    """写入 PrintJob.content 前：清控制字符并截断。"""
    s = _strip_print_controls(content)
    if len(s) > PRINT_CONTENT_MAX_LEN:
        return s[:PRINT_CONTENT_MAX_LEN]
    return s


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _escape_print_text(text: str) -> str:
    """清除 ESC/GS/控制字符并转义 < >，避免用户输入注入打印机指令或标签。"""
    s = _strip_print_controls(text)
    # 显式去掉 ESC(0x1b) / GS(0x1d)（已含于上式）；再挡常见标签注入
    return s.replace("<", "&lt;").replace(">", "&gt;")


def _barcode_payload(order_no: str) -> str:
    """条码可编码内容：只保留字母数字并大写（订单号本身已是此格式）。"""
    s = re.sub(r"[^A-Za-z0-9]", "", (order_no or "").strip().upper())
    return s or "0"


def _print_text_width(text: str) -> int:
    """估算热敏纸上的「半角列」宽度：ASCII=1，中日韩等全角≈2。

    用于菜名与 x份数 同一行时算空格，避免中文把排版撑乱。
    """
    w = 0
    for ch in text or "":
        o = ord(ch)
        w += 2 if o > 0x7F else 1
    return w


def _kitchen_item_name_qty_line(name: str, qty: int, *, cols: int = 24) -> str:
    """菜品行：菜名靠左、数量靠右，整行 <B2> 加大。

    cols=24：倍高倍宽时 80mm 纸大约能排这么多半角列；改字号/机型可调此参数。
    菜名过长会按显示宽度截断，保证「x1」仍能印在同一行右侧。
    """
    left = (name or "商品").strip() or "商品"
    right = f"x{max(1, int(qty or 1))}"
    max_left = max(4, cols - _print_text_width(right) - 1)
    # 超长截断（按显示宽度，不是按字符个数）
    while left and _print_text_width(left) > max_left:
        left = left[:-1]
    left = left or "商品"
    gap = max(1, cols - _print_text_width(left) - _print_text_width(right))
    body = f"{left}{' ' * gap}{right}"
    return f"<B2>{_escape_print_text(body)}</B2>"


def _format_options(opts: list[OrderItemOption]) -> str:
    """把加料/口味选项拼成一行，如：大碗、加牛肉、微辣。

    数量>1 时写成「选项名x2」。无选项返回空串（调用方不打印该行）。
    """
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
    """订单渠道 → 中文展示名；未知渠道则原样大写，再不行显示「门店」。"""
    ch = (order.channel or order.payment_method or "").upper()
    return _CHANNEL_LABEL.get(ch, ch or "门店")


# ===========================================================================
# 票头排版（改样式主要看这一节）
#
# 组装入口：build_ticket_content() 里
#   lines = [_header_line(...), _meta_line(...), 分隔线, ...]
# ===========================================================================


def _header_line(ctx: OrderPrintContext, ticket_type: str, store_name: str) -> str:
    """制作单第 1 行：店名 +「厨房制作单」（居中加粗）。

    想改文案：改下面的 title，例如 f\"{name}-制作单\"。
    催单/作废：在同一行加【催单】【作废】前缀。
    """
    name = _escape_print_text((store_name or "").strip() or "SmartNoodleHouse")
    title = f"{name} 厨房制作单"
    if ticket_type == TICKET_URGE:
        return f"<C><B>【催单】{title}</B></C>"
    if ticket_type == TICKET_CANCEL:
        return f"<C><B>【作废】{title}</B></C>"
    return f"<C><B>{title}</B></C>"


def _dining_highlight(ctx: OrderPrintContext) -> str:
    """制作单第 2 行里「加大字」那一段（不含时间）。

    按订单类型返回：
      - 堂食：堂食 + 桌名（优先 table_name，否则 table_no）
      - 外带：外带 取餐码 xxx
      - 外卖：平台名 + #当日序号
      - 兜底：从单号解析桌位，或显示渠道名

    想改叫法（如「堂食」→「店内」）改对应 return 即可。
    """
    order = ctx.order
    # 外卖：有 delivery 记录优先
    if ctx.delivery:
        seq = (ctx.delivery.platform_day_seq or "").strip()
        platform = _channel_label(order)
        return f"{platform} #{seq}" if seq else platform
    # 堂食：有桌台且不是外带
    if ctx.table and (order.dining_type or "").upper() != "TAKEAWAY":
        label = (ctx.table.table_name or ctx.table.table_no or "").strip()
        return f"堂食 {label}" if label else "堂食"
    # 外带：取餐码（从订单号规则推导）
    if (order.dining_type or "").upper() == "TAKEAWAY":
        code = pickup_code_from_order_no(order.order_no)
        return f"外带 取餐码 {code}"
    # 无桌台对象时，尝试从业务单号里解桌位段
    parsed = parse_biz_no(order.order_no or "")
    if parsed and parsed.get("table") not in ("000", "DLV", "TKW"):
        return f"堂食 {parsed['table']}"
    return _channel_label(order)


def _meta_line(ctx: OrderPrintContext, *, cols: int = 48) -> str:
    """制作单第 2 行：时间靠左，堂食+桌名靠右（同一行）。

    【改时间格式】只改下面 strftime：
      %Y-%m-%d %H:%M:%S  → 2026-09-05 14:51:08（当前带秒）
      只要到分钟：改成 \"%Y-%m-%d %H:%M\"

    【改左右布局】
      cols=48：80mm 正常字号约半角列数；印偏了可改 42~48
      右侧 <B>…</B>：与第1行同字号加粗；不要用 <B2>（会倍高倍宽）
    """
    # ↓↓↓ 时间格式：当前带秒；自行改 strftime 即可
    ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
    left = f"时间：{ts}"
    right = _escape_print_text(_dining_highlight(ctx))
    # 右侧过长则截断，保证仍能与时间同一行左右排开
    max_right = max(4, cols - _print_text_width(left) - 1)
    while right and _print_text_width(right) > max_right:
        right = right[:-1]
    right = right or "堂食"
    gap = max(1, cols - _print_text_width(left) - _print_text_width(right))
    return f"{left}{' ' * gap}<B>{right}</B>"


def _order_no_footer_lines(order_no: str, *, mode: str | None = None) -> list[str]:
    """制作单底部「订单号」区域，受 kitchen_ticket_order_no（库表/ .env）控制。

    - barcode（默认）：先打 CODE-128，再打可读单号
    - text / plain / number：只打可读单号
    - off / none / 0 / false / no：不打印订单号区域

    条码参数说明（改宽高主要改这里）：
      w=\"2\"  模块宽（芯烨合法 2~6；过窄会打不出，过宽会超纸宽）
      h=\"64\" 条码高度（点）
      p=\"0\"  HRI 人眼可读字符位置（0=不打印在条码上，我们另起一行打单号）
    """
    settings = get_settings()
    resolved = (mode if mode is not None else settings.kitchen_ticket_order_no or "barcode")
    resolved = str(resolved).strip().lower()
    if resolved in ("off", "none", "0", "false", "no"):
        return []
    no = (order_no or "").strip().upper()
    if not no:
        return []
    safe = _escape_print_text(no)
    if resolved in ("text", "plain", "number"):
        return [f"<C>{safe}</C>"]
    # 默认 barcode：条码 + 下方文字单号（具体 ESC/POS 编码见 escpos_client）
    payload = _barcode_payload(no)
    return [
        f'<C><BARCODE t="CODE128" w="2" h="64" p="0">{payload}</BARCODE></C>',
        f"<C>{safe}</C>",
    ]


def build_ticket_content(
    ctx: OrderPrintContext,
    ticket_type: str = TICKET_KITCHEN,
    *,
    kitchen_ticket_order_no: str | None = None,
) -> str:
    """生成厨房小票全文，各行用 <BR> 拼接，末尾 <CUT>。

    结构顺序：
      1) 票头第1行：店名 + 厨房制作单
      2) 票头第2行：时间 + 加大堂食/桌名
      3) 分隔线
      4) 菜品（倍高倍宽）+ 选项 + 单品备注
      5) 外卖时再打顾客/地址
      6) 整单备注（若有）
      7) 订单号条码/文字（受开关控制）
      8) 切纸

    催单/作废票：票头带前缀；正文里多打一行「单号」，不按制作单筛菜逻辑展开
    （当前 else 分支较简，主要给非 KITCHEN 类型用）。
    """
    order = ctx.order
    store_name = (ctx.store.name if ctx.store else "").strip() or "SmartNoodleHouse"
    # —— 票头两行 + 分隔线（改样式见上方 _header_line / _meta_line）——
    # 中间空一行：避免「店名制作单」与「时间/堂食」贴太紧
    lines: list[str] = [
        _header_line(ctx, ticket_type, store_name),
        "",
        _meta_line(ctx),
        _LINE,
    ]

    if ticket_type == TICKET_KITCHEN:
        # 制作单：默认跳过「无需制作」商品；若筛完为空则退回全部（避免空白票）
        kitchen_items = [
            item
            for item in ctx.items
            if not ctx.no_cooking_by_product.get(int(item.product_id or 0), False)
        ]
        if not kitchen_items:
            kitchen_items = ctx.items
        for item in kitchen_items:
            name = item.product_name_snapshot or "商品"
            qty = int(item.quantity or 1)
            # 菜名 x数量（加大）
            lines.append(_kitchen_item_name_qty_line(name, qty))
            # 口味/加料（缩进两格，普通字号）
            opts = _format_options(ctx.options_by_item.get(item.id, []))
            if opts:
                lines.append(f"  {_escape_print_text(opts)}")
            # 单品备注
            item_remark = (item.remark or "").strip()
            if item_remark:
                lines.append(f"  注:{_escape_print_text(item_remark)}")
        lines.append(_LINE)
        # 外卖附加：顾客与地址（堂食无 delivery 则跳过）
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
        # 催单/作废等：正文简化，至少带上单号便于核对
        lines.append(f"单号 {_escape_print_text(order.order_no)}")
        lines.append(_LINE)

    # 整单备注（仅制作单）
    remark = (order.remark or "").strip()
    if remark and ticket_type == TICKET_KITCHEN:
        lines.append(f"备注 {_escape_print_text(remark[:200])}")
        lines.append(_LINE)

    # 票底订单号 / 条码
    order_no = (order.order_no or "").strip().upper()
    lines.extend(_order_no_footer_lines(order_no, mode=kitchen_ticket_order_no))
    lines.append("<CUT>")
    return "<BR>".join(lines)


class PrintService:
    """打印门面：解析打印机配置 → 装上下文 → 组票 → 发送。"""

    @classmethod
    async def resolve_printer_host(cls, db: AsyncSession, store_id: int | None) -> str:
        """解析局域网打印机 IP。

        优先级：门店 store_settings.printer_host > 全局 system_settings / .env PRINTER_HOST。
        """
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        return (
            await SystemSettingsService.effective_setting(
                db,
                "printer_host",
                store_id=int(store_id) if store_id else None,
                fallback=settings.printer_host or "",
            )
        ).strip()

    @classmethod
    async def resolve_printer_sn(cls, db: AsyncSession, store_id: int | None) -> str:
        """解析芯烨云打印机 SN（仅 PRINTER_MODE=xpyun 时需要）。

        优先级：门店 store_settings.xpyun_printer_sn > 全局 XPYUN_PRINTER_SN（密钥仍在 .env）。
        """
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
        """按订单 id 装齐打印上下文；订单不存在返回 None。"""
        order = await db.get(Order, order_id)
        if not order:
            return None
        # 商品行（按 id 稳定排序，保证补打顺序一致）
        items = list(
            (await db.execute(select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id))).scalars().all()
        )
        item_ids = [i.id for i in items]
        # 选项按 order_item_id 分组
        opts_by_item: dict[int, list[OrderItemOption]] = {}
        if item_ids:
            for opt in (
                await db.execute(select(OrderItemOption).where(OrderItemOption.order_item_id.in_(item_ids)))
            ).scalars().all():
                opts_by_item.setdefault(opt.order_item_id, []).append(opt)
        # 商品「无需制作」标记，制作单用来过滤
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
    def _mark_job_success(cls, job: PrintJob) -> None:
        job.status = "SUCCESS"
        job.succeeded_at = now_local()
        job.last_error = None
        job.next_retry_at = None
        job.locked_at = None
        job.locked_by = None

    @classmethod
    def _mark_job_failed(cls, job: PrintJob, exc: BaseException) -> None:
        err = str(exc)[:500] or exc.__class__.__name__
        attempts = int(job.attempt_count or 1)
        job.last_error = err
        job.locked_at = None
        job.locked_by = None
        if attempts < MAX_PRINT_ATTEMPTS:
            job.status = "RETRYING"
            job.next_retry_at = now_local() + timedelta(seconds=_print_backoff_seconds(attempts))
        else:
            job.status = "FAILED"
            job.next_retry_at = None

    @classmethod
    async def _upsert_pending_job(
        cls,
        db: AsyncSession,
        *,
        store_id: int,
        order_id: int,
        ticket_type: str,
        content: str,
        print_job_id: int | None = None,
    ) -> PrintJob:
        """创建或更新 PrintJob 为 PENDING，写入已 sanitize 的 content。

        P1 修复：按 (order_id, ticket_type) 查找现有 PENDING/PRINTING 任务，
        存在则复用，避免超时重试重复出票。
        """
        safe = _prepare_job_content(content)
        digest = _content_hash(safe)
        job: PrintJob | None = None
        if print_job_id:
            job = await db.get(PrintJob, int(print_job_id))
        if job is None:
            # 幂等：复用同订单同票型的未完成打印任务，避免重复出票
            existing = (
                await db.execute(
                    select(PrintJob).where(
                        PrintJob.order_id == int(order_id),
                        PrintJob.ticket_type == ticket_type,
                        PrintJob.status.in_(("PENDING", "PRINTING")),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                job = existing
        if job is None:
            job = PrintJob(
                store_id=int(store_id),
                order_id=int(order_id),
                ticket_type=ticket_type,
                content=safe,
                content_hash=digest,
                status="PENDING",
                attempt_count=0,
            )
            db.add(job)
            await db.flush()
        else:
            job.store_id = int(store_id)
            job.order_id = int(order_id)
            job.ticket_type = ticket_type
            job.content = safe
            job.content_hash = digest
            job.status = "PENDING"
            job.next_retry_at = None
            job.last_error = None
            job.locked_at = None
            job.locked_by = None
            await db.flush()
        return job

    @classmethod
    async def _begin_printing(cls, db: AsyncSession, job: PrintJob) -> None:
        job.status = "PRINTING"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.locked_at = now_local()
        job.locked_by = _print_worker_id()[:64]
        job.next_retry_at = None
        await db.flush()

    @classmethod
    async def _send_content(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        order_no: str,
        content: str,
        ticket_type: str,
    ) -> dict[str, Any]:
        """按配置把已清洗的 content 发到打印机；失败抛 AppException。"""
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        sn = await cls.resolve_printer_sn(db, store_id)
        host = await cls.resolve_printer_host(db, store_id)
        mode = (
            await SystemSettingsService.effective_setting(
                db, "printer_mode", store_id=store_id, fallback=settings.printer_mode or "local"
            )
        ).strip().lower()
        safe = _strip_print_controls(content)
        copies = await SystemSettingsService.effective_int(
            db, "xpyun_print_copies", settings.xpyun_print_copies, store_id=store_id
        )
        voice = await SystemSettingsService.effective_int(
            db, "xpyun_voice", settings.xpyun_voice, store_id=store_id
        )
        printer_port = await SystemSettingsService.effective_int(
            db, "printer_port", settings.printer_port, store_id=store_id
        )

        # ---------- 芯烨云模式 ----------
        if mode == "xpyun":
            if not sn or not (settings.xpyun_user or "").strip() or not (settings.xpyun_user_key or "").strip():
                if settings.allow_print_mock:
                    logger.warning(
                        "MOCK 打印 ticket=%s order=%s sn=%s\n%s",
                        ticket_type,
                        order_no,
                        sn or "(未配置)",
                        safe.replace("<BR>", "\n"),
                    )
                    return {"mock": True, "order_no": order_no}
                raise AppException(
                    50040,
                    f"芯烨云未配置完整（USER/USER_KEY/打印机SN），无法出票 order={order_no}",
                )
            client = XpyunClient()
            data = await client.print_ticket(
                sn,
                safe,
                copies=copies,
                cutter=1,
                voice=0 if ticket_type != TICKET_KITCHEN else voice,
                mode=1,
            )
            logger.info("厨房小票已发送(云) order=%s sn=%s type=%s", order_no, sn, ticket_type)
            return data

        # ---------- 局域网 ESC/POS（XP-80T 等）----------
        if not host:
            if settings.allow_print_mock:
                logger.warning(
                    "MOCK 打印 ticket=%s order=%s host=%s\n%s",
                    ticket_type,
                    order_no,
                    host or "(未配置)",
                    safe.replace("<BR>", "\n"),
                )
                return {"mock": True, "order_no": order_no}
            raise AppException(
                50040,
                f"未配置打印机 IP（PRINTER_HOST），无法出票 order={order_no}",
            )

        client = EscposClient(host=host, port=printer_port)
        try:
            data = await client.print_ticket(safe, copies=copies)
        except RuntimeError as exc:
            raise AppException(
                PRINTER_UNREACHABLE,
                str(exc) or f"无法连接打印机 {host}:{printer_port}",
            ) from exc
        logger.info("厨房小票已发送(局域网) order=%s host=%s type=%s", order_no, host, ticket_type)
        return data

    @classmethod
    async def print_order(
        cls,
        db: AsyncSession,
        order_id: int,
        *,
        ticket_type: str = TICKET_KITCHEN,
        print_job_id: int | None = None,
    ) -> dict[str, Any] | None:
        """执行一次出票（落库 PrintJob：PENDING → PRINTING → SUCCESS / RETRYING|FAILED）。

        返回：
          - dict：云打印/局域网成功结果，或 mock 结果（含 print_job_id）
          - None：总开关关闭、订单不存在、已取消/退款制作单等「业务跳过」

        异常：
          - AppException：配置缺失、打印机连不上等（接口应转成业务错误码，而非裸 500）
        """
        settings = get_settings()
        from app.services.system_settings_service import SystemSettingsService

        # 总开关（历史字段名 XPYUN_ENABLED，对 local / xpyun 两种模式都生效）
        enabled = await SystemSettingsService.effective_bool(
            db, "xpyun_enabled", settings.xpyun_enabled, store_id=None
        )
        if not enabled:
            logger.debug("打印已关闭 xpyun_enabled=false order_id=%s", order_id)
            return None

        ctx = await cls.load_context(db, order_id)
        if not ctx:
            logger.warning("打印跳过：订单不存在 order_id=%s", order_id)
            return None

        # 已取消/退款：不再打制作单（催单/作废类型仍可由上层决定是否调用）
        if ticket_type == TICKET_KITCHEN and ctx.order.status in ("CANCELLED", "REFUNDED"):
            logger.info("打印跳过：订单已取消 order_id=%s", order_id)
            return None

        content = build_ticket_content(
            ctx,
            ticket_type=ticket_type,
            kitchen_ticket_order_no=await SystemSettingsService.effective_setting(
                db,
                "kitchen_ticket_order_no",
                store_id=int(ctx.order.store_id),
                fallback=settings.kitchen_ticket_order_no,
            ),
        )
        job = await cls._upsert_pending_job(
            db,
            store_id=int(ctx.order.store_id),
            order_id=int(order_id),
            ticket_type=ticket_type,
            content=content,
            print_job_id=print_job_id,
        )
        await cls._begin_printing(db, job)

        try:
            data = await cls._send_content(
                db,
                store_id=ctx.order.store_id,
                order_no=ctx.order.order_no or "",
                content=job.content or content,
                ticket_type=ticket_type,
            )
            cls._mark_job_success(job)
            await db.flush()
            if isinstance(data, dict):
                data = {**data, "print_job_id": job.id}
            return data
        except Exception as exc:
            cls._mark_job_failed(job, exc)
            await db.flush()
            raise

    @classmethod
    async def claim_print_job(cls, db: AsyncSession, job_id: int, *, worker: str | None = None) -> PrintJob | None:
        """原子 claim：RETRYING/FAILED 且到点可重试 → PRINTING。"""
        wid = (worker or _print_worker_id())[:64]
        now = now_local()
        stmt = (
            update(PrintJob)
            .where(
                PrintJob.id == int(job_id),
                PrintJob.attempt_count < MAX_PRINT_ATTEMPTS,
                PrintJob.status.in_(("RETRYING", "FAILED")),
                or_(
                    PrintJob.next_retry_at.is_(None),
                    PrintJob.next_retry_at <= now,
                ),
            )
            .values(
                status="PRINTING",
                locked_at=now,
                locked_by=wid,
                attempt_count=PrintJob.attempt_count + 1,
                next_retry_at=None,
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        if not result.rowcount:
            return None
        db.expire_all()
        return await db.get(PrintJob, int(job_id))

    @classmethod
    async def process_print_job(cls, job_id: int) -> bool:
        """消费一条可重试 PrintJob：claim 后用已存 content 重打。

        claim 释放使用原子 UPDATE WHERE locked_by = wid：处理期间若行被
        reclaim_stale_printing 复位并被另一 worker 重新 claim，原 worker 的
        UPDATE 命中 0 行，不会覆盖新 owner 的锁。
        """
        wid = _print_worker_id()
        async with AsyncSessionLocal() as db:
            job = await cls.claim_print_job(db, job_id, worker=wid)
            if not job:
                return False
            order = await db.get(Order, job.order_id)
            order_no = (order.order_no if order else "") or ""
            try:
                data = await cls._send_content(
                    db,
                    store_id=job.store_id,
                    order_no=order_no,
                    content=job.content or "",
                    ticket_type=job.ticket_type or TICKET_KITCHEN,
                )
                # 原子释放：仅当 locked_by 仍为本 worker 时才标记 SUCCESS
                success_stmt = (
                    update(PrintJob)
                    .where(
                        PrintJob.id == int(job_id),
                        PrintJob.locked_by == wid,
                    )
                    .values(
                        status="SUCCESS",
                        succeeded_at=now_local(),
                        last_error=None,
                        next_retry_at=None,
                        locked_at=None,
                        locked_by=None,
                    )
                )
                ok_result = await db.execute(success_stmt)
                await db.commit()
                if not ok_result.rowcount:
                    logger.warning(
                        "PrintJob 行在处理期间被 reclaim 释放 id=%s", job_id
                    )
                    return False
                logger.info(
                    "PrintJob 重试成功 id=%s order_id=%s mock=%s",
                    job.id,
                    job.order_id,
                    bool(isinstance(data, dict) and data.get("mock")),
                )
                return True
            except Exception as exc:
                logger.exception("PrintJob 重试失败 id=%s: %s", job_id, exc)
                async with AsyncSessionLocal() as db2:
                    row = await db2.get(PrintJob, int(job_id))
                    if not row or row.status == "SUCCESS":
                        return False
                    err = str(exc)[:500] or exc.__class__.__name__
                    attempts = int(row.attempt_count or 1)
                    retry_at = (
                        now_local() + timedelta(seconds=_print_backoff_seconds(attempts))
                        if attempts < MAX_PRINT_ATTEMPTS
                        else None
                    )
                    new_status = "RETRYING" if attempts < MAX_PRINT_ATTEMPTS else "FAILED"
                    # 原子释放：仅当 locked_by 仍为原 worker 时才标记失败
                    fail_stmt = (
                        update(PrintJob)
                        .where(
                            PrintJob.id == int(job_id),
                            PrintJob.locked_by == wid,
                        )
                        .values(
                            status=new_status,
                            last_error=err,
                            next_retry_at=retry_at,
                            locked_at=None,
                            locked_by=None,
                        )
                    )
                    fail_result = await db2.execute(fail_stmt)
                    await db2.commit()
                    if not fail_result.rowcount:
                        logger.warning(
                            "PrintJob 行在失败处理期间被 reclaim 释放 id=%s", job_id
                        )
                return False

    @classmethod
    async def process_pending_print_jobs(cls, limit: int = 20) -> int:
        """扫描 RETRYING/FAILED 且 next_retry_at<=now 的任务并逐条 claim 重打。"""
        now = now_local()
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(PrintJob.id)
                    .where(
                        PrintJob.status.in_(("RETRYING", "FAILED")),
                        PrintJob.attempt_count < MAX_PRINT_ATTEMPTS,
                        or_(
                            PrintJob.next_retry_at.is_(None),
                            PrintJob.next_retry_at <= now,
                        ),
                    )
                    .order_by(PrintJob.id)
                    .limit(limit)
                )
            ).scalars().all()
            ids = list(rows)
        done = 0
        for jid in ids:
            if await cls.process_print_job(int(jid)):
                done += 1
        return done

    @classmethod
    async def reclaim_stale_printing(cls, db: AsyncSession, *, stale_after: int = STALE_PRINTING_SEC) -> int:
        """回收崩溃残留的 PRINTING 任务：locked_at < now - stale_after → 复位 RETRYING。

        复位时不清零 attempt_count（避免无限重试），仅释放锁。
        注意：必须回到 RETRYING（而非 PENDING）——process_pending_print_jobs
        只认领 RETRYING/FAILED，回到 PENDING 会导致任务永久卡住。
        """
        cutoff = now_local() - timedelta(seconds=stale_after)
        stmt = (
            update(PrintJob)
            .where(
                PrintJob.status == "PRINTING",
                or_(PrintJob.locked_at.is_(None), PrintJob.locked_at < cutoff),
            )
            .values(
                status="RETRYING",
                locked_at=None,
                locked_by=None,
                next_retry_at=now_local(),
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        count = int(result.rowcount or 0)
        if count:
            logger.warning("Reclaimed %s stale PRINTING print jobs", count)
        return count
