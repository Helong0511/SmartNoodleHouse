"""顾客叫号屏：脱敏展示字段与状态文案。"""
from __future__ import annotations

from app.core.biz_no import parse_biz_no
from app.models import DiningTable, Order

DISPLAY_ACTIVE_STATUSES = ("PAID", "CONFIRMED", "PREPARING", "COOKING", "READY")
PREPARING_STATUSES = ("PAID", "CONFIRMED", "PREPARING", "COOKING")

CUSTOMER_STATUS_LABEL: dict[str, str] = {
    "PAID": "排队中",
    "CONFIRMED": "排队中",
    "PREPARING": "制作中",
    "COOKING": "制作中",
    "READY": "请取餐",
}


def pickup_code_from_order_no(order_no: str) -> str:
    parsed = parse_biz_no(order_no or "")
    if parsed:
        return parsed["rand"]
    s = (order_no or "").strip().upper()
    return s[-4:] if len(s) >= 4 else (s or "-")


def build_display_item(order: Order, table: DiningTable | None) -> dict:
    is_takeaway = (order.dining_type or "").upper() == "TAKEAWAY"
    code = pickup_code_from_order_no(order.order_no)
    if table and not is_takeaway:
        pickup_label = (table.table_name or table.table_no or f"桌{order.table_id}").strip()
        pickup_sub = "堂食"
        mode = "DINE_IN"
    else:
        pickup_label = code
        pickup_sub = "打包自取" if is_takeaway else "取餐"
        mode = "TAKEAWAY" if is_takeaway else "PICKUP"
    status = order.status or ""
    return {
        "id": order.id,
        "pickup_code": code,
        "pickup_label": pickup_label,
        "pickup_sub": pickup_sub,
        "mode": mode,
        "status": status,
        "status_label": CUSTOMER_STATUS_LABEL.get(status, status),
        "dining_type": order.dining_type,
        "is_takeaway": is_takeaway,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
    }


def is_in_store_order(order: Order) -> bool:
    return (order.channel or "") not in ("MEITUAN", "ELEME")


def pickup_fields(order: Order, table: DiningTable | None) -> dict:
    """小程序/顾客端：取餐号与叫号屏对照字段。"""
    if not is_in_store_order(order):
        return {
            "pickup_code": None,
            "pickup_label": None,
            "pickup_sub": None,
            "customer_status_label": None,
            "show_pickup_banner": False,
        }
    item = build_display_item(order, table)
    status = order.status or ""
    return {
        "pickup_code": item["pickup_code"],
        "pickup_label": item["pickup_label"],
        "pickup_sub": item["pickup_sub"],
        "customer_status_label": CUSTOMER_STATUS_LABEL.get(status, status),
        "show_pickup_banner": status in DISPLAY_ACTIVE_STATUSES,
    }
