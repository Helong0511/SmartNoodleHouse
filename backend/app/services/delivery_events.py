"""外卖平台回调事件分类"""
from __future__ import annotations

from typing import Any


class DeliveryEvent:
    ORDER_NEW = "ORDER_NEW"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    ORDER_COMPLETED = "ORDER_COMPLETED"
    ORDER_CANCEL = "ORDER_CANCEL"
    REFUND = "REFUND"
    URGE = "URGE"
    DELIVERY = "DELIVERY"
    PRIVACY = "PRIVACY"
    UNKNOWN = "UNKNOWN"


def classify_meituan_event(data: dict[str, Any]) -> str:
    notify = str(data.get("notify_type") or data.get("event") or data.get("type") or "").lower()
    if "urge" in notify or "催" in notify or data.get("urge_id"):
        return DeliveryEvent.URGE
    if "refund" in notify or "退款" in notify or data.get("refund_id"):
        return DeliveryEvent.REFUND
    if "privacy" in notify or "隐私" in notify:
        return DeliveryEvent.PRIVACY
    if data.get("logistics_status") or data.get("logistics_code") or "delivery" in notify or "配送" in notify:
        return DeliveryEvent.DELIVERY

    status = data.get("status")
    try:
        s = int(status)
        if s == 9:
            return DeliveryEvent.ORDER_CANCEL
        if s == 8:
            return DeliveryEvent.ORDER_COMPLETED
        if s == 4:
            return DeliveryEvent.ORDER_CONFIRMED
        if s in (1, 2):
            return DeliveryEvent.ORDER_NEW
    except (TypeError, ValueError):
        pass

    if "cancel" in notify:
        return DeliveryEvent.ORDER_CANCEL
    if "complete" in notify or "finish" in notify:
        return DeliveryEvent.ORDER_COMPLETED
    if data.get("_dev_simulate") and data.get("items"):
        return DeliveryEvent.ORDER_NEW
    if data.get("order_id") or data.get("wm_order_id_view"):
        return DeliveryEvent.ORDER_NEW
    return DeliveryEvent.UNKNOWN


def classify_eleme_event(data: dict[str, Any]) -> str:
    t = str(data.get("type") or data.get("event") or "")
    tl = t.lower()
    if t in ("217",) or "new" in tl and "order" in tl:
        return DeliveryEvent.ORDER_NEW
    if t in ("14", "15", "17") or "cancel" in tl:
        return DeliveryEvent.ORDER_CANCEL
    if "refund" in tl or t in ("20", "21", "22"):
        return DeliveryEvent.REFUND
    if "urge" in tl or "remind" in tl or t == "218":
        return DeliveryEvent.URGE
    if "privacy" in tl or "隐私" in tl or t in ("233",):
        return DeliveryEvent.PRIVACY
    if "delivery" in tl or "logistics" in tl or t in ("231", "232"):
        return DeliveryEvent.DELIVERY
    if "complete" in tl or t == "219":
        return DeliveryEvent.ORDER_COMPLETED
    if data.get("_dev_simulate") and (data.get("message") or data.get("items")):
        return DeliveryEvent.ORDER_NEW
    return DeliveryEvent.UNKNOWN


EVENT_WS_MAP = {
    DeliveryEvent.ORDER_NEW: "ORDER_NEW",
    DeliveryEvent.ORDER_CONFIRMED: "ORDER_CONFIRMED",
    DeliveryEvent.ORDER_COMPLETED: "ORDER_COMPLETED",
    DeliveryEvent.ORDER_CANCEL: "ORDER_CANCELLED",
    DeliveryEvent.REFUND: "ORDER_REFUND",
    DeliveryEvent.URGE: "ORDER_URGE",
    DeliveryEvent.DELIVERY: "DELIVERY_UPDATE",
    DeliveryEvent.PRIVACY: "DELIVERY_PRIVACY",
    DeliveryEvent.UNKNOWN: "DELIVERY_UNKNOWN",
}


def parse_meituan_foods(data: dict[str, Any]) -> list[dict]:
    """解析美团 detail 字段（官方为 JSON 数组字符串）"""
    detail = data.get("detail")
    if isinstance(detail, str):
        import json

        try:
            detail = json.loads(detail)
        except Exception:
            detail = []
    if isinstance(detail, dict):
        detail = detail.get("foods") or detail.get("items") or [detail]
    if not isinstance(detail, list):
        detail = data.get("items") or []

    items = []
    for it in detail:
        if not isinstance(it, dict):
            continue
        spec = it.get("spec") or it.get("food_property") or ""
        options = []
        if spec:
            options.append({"group": "规格", "value": spec, "code": spec})
        for attr in it.get("attrs") or []:
            if isinstance(attr, dict):
                options.append({"group": attr.get("name", "属性"), "value": attr.get("value", ""), "code": attr.get("code", "")})
        items.append(
            {
                "platform_sku_id": str(it.get("sku_id") or it.get("app_food_code") or ""),
                "app_food_code": str(it.get("app_food_code") or it.get("sku_id") or ""),
                "name": it.get("food_name") or it.get("name") or "外卖商品",
                "quantity": int(it.get("quantity") or it.get("num") or 1),
                "price": it.get("price") or it.get("food_price") or "0",
                "box_price": it.get("box_price") or "0",
                "options": options,
            }
        )
    return items
