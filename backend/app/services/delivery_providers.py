"""美团 / 饿了么外卖渠道

官方文档：
- 美团接单: https://waimaiopen.meituan.com/api/v1/order/confirm
- 美团出餐: https://waimaiopen.meituan.com/api/v1/order/preparationMealComplete
- 饿了么接单: eleme.order.confirmOrderLite
- 饿了么出餐: eleme.order.setOrderPrepared
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

import httpx

from app.core.http_client import get_http_client

from app.core.config import get_settings
from app.services.delivery_context import PlatformCredentials
from app.services.delivery_sign import (
    eleme_shop_sign,
    flatten_form_body,
    meituan_sign,
    meituan_verify_callback,
)

logger = logging.getLogger(__name__)


def _ok_or_mock(data: dict, *, ok_expr: bool) -> bool:
    """平台真实成功才算成功；mock 仅 allow_delivery_mock 时为 True。"""
    if data.get("_mock"):
        return bool(get_settings().allow_delivery_mock)
    return ok_expr



def _dev_mode_allowed() -> bool:
    from app.core.config import dev_mode_allowed

    return dev_mode_allowed("allow_auth_mock")


class DeliveryProvider(ABC):
    platform: str

    def __init__(self, credentials: PlatformCredentials | None = None):
        self.credentials = credentials or PlatformCredentials()

    @abstractmethod
    def verify_notify(self, headers: dict, body: bytes, notify_url: str = "") -> dict[str, Any]:
        ...

    @abstractmethod
    def parse_order(self, data: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    async def confirm_order(self, platform_order_id: str) -> bool:
        ...

    @abstractmethod
    async def sync_status(self, platform_order_id: str, status: str) -> bool:
        ...

    async def reply_urge(self, platform_order_id: str, raw: dict[str, Any]) -> bool:
        logger.info("[%s] 催单已记录 order=%s", self.platform, platform_order_id)
        return True

    async def agree_refund(self, refund_id: str, platform_order_id: str) -> bool:
        logger.info("[%s] 同意退款 refund=%s order=%s", self.platform, refund_id, platform_order_id)
        return True

    async def disagree_refund(self, refund_id: str, platform_order_id: str, reason: str) -> bool:
        logger.info("[%s] 拒绝退款 refund=%s order=%s reason=%s", self.platform, refund_id, platform_order_id, reason)
        return True

    async def sync_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        """同步单个餐品到平台。返回 {"ok": bool, "platform_id": str|None, "error": str|None}。
        platform_id 用于落库到 DeliveryProductMap.platform_sku_id，便于后续 updateItem/price/stock。
        """
        logger.info("[%s mock] sync_product %s", self.platform, payload.get("name"))
        return {"ok": True, "platform_id": None}

    async def sync_prices(self, items: list[dict]) -> int:
        logger.info("[%s mock] sync_prices count=%s", self.platform, len(items))
        return len(items)

    async def sync_stock(self, items: list[dict]) -> int:
        logger.info("[%s mock] sync_stock count=%s", self.platform, len(items))
        return len(items)

    async def cancel_order(self, platform_order_id: str, reason: str = "商家取消") -> bool:
        logger.info("[%s] 取消订单 order=%s reason=%s", self.platform, platform_order_id, reason)
        return True

    # ── 菜单 / 库存 / 营业状态（步骤1：基类默认 mock；步骤2：平台子类真实实现）──

    async def batch_sync_products(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        """批量创建/更新菜品。返回 {"ok": n, "fail": n, "errors": [...]}。"""
        logger.info("[%s mock] batch_sync_products count=%s", self.platform, len(payloads))
        return {"ok": len(payloads), "fail": 0, "errors": []}

    async def set_sell_status(self, items: list[dict[str, Any]], on_shelf: bool) -> int:
        """批量上架/下架。items: [{"app_food_code", "platform_sku_id"?}]。返回成功条数。"""
        logger.info("[%s mock] set_sell_status on_shelf=%s count=%s", self.platform, on_shelf, len(items))
        return len(items)

    async def set_sku_stock(self, items: list[dict[str, Any]]) -> int:
        """绝对覆盖 SKU 库存（售罄置0 / 对账主用）。items: [{"app_food_code","stock"}]。"""
        logger.info("[%s mock] set_sku_stock count=%s", self.platform, len(items))
        return len(items)

    async def inc_stock(self, items: list[dict[str, Any]]) -> int:
        """原子增加库存 items: [{"app_food_code","qty"}]。"""
        logger.info("[%s mock] inc_stock count=%s", self.platform, len(items))
        return len(items)

    async def dec_stock(self, items: list[dict[str, Any]]) -> int:
        """原子扣减库存 items: [{"app_food_code","qty"}]。"""
        logger.info("[%s mock] dec_stock count=%s", self.platform, len(items))
        return len(items)

    async def set_store_open_status(self, open_status: str, shipping_time: str | None = None) -> bool:
        """同步门店营业状态。open_status: OPEN(营业)/REST(休息)/OFFLINE(下线)；shipping_time 营业时间串。"""
        logger.info(
            "[%s mock] set_store_open_status status=%s hours=%s", self.platform, open_status, shipping_time
        )
        return True


class MeituanProvider(DeliveryProvider):
    """美团外卖开放平台 waimaiopen"""

    platform = "MEITUAN"

    def verify_notify(self, headers: dict, body: bytes, notify_url: str = "") -> dict[str, Any]:
        settings = get_settings()
        text = body.decode("utf-8")
        if text.strip().startswith("{"):
            data = json.loads(text)
            if data.get("_dev_simulate") and _dev_mode_allowed():
                logger.warning("美团开发模式：跳过验签")
                return data
            params = {str(k): "" if v is None else str(v) if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False) for k, v in data.items()}
            raw_data = data
        else:
            params = flatten_form_body(body)
            raw_data = dict(params)
            if params.get("_dev_simulate") in ("true", "1") and _dev_mode_allowed():
                logger.warning("美团开发模式：跳过验签")
                return raw_data

        if not settings.meituan_app_secret:
            raise ValueError("美团未配置，拒绝回调")

        url = notify_url or settings.meituan_notify_url
        if not url:
            raise ValueError("未配置 MEITUAN_NOTIFY_URL，无法验签")

        content_type = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
        double_decode = "json" not in content_type
        ok = meituan_verify_callback(url, params, settings.meituan_app_secret, double_decode=double_decode)
        if not ok:
            ok = meituan_verify_callback(url, params, settings.meituan_app_secret, double_decode=False)
        if not ok:
            raise ValueError("美团签名验证失败")

        for key in ("order", "detail", "food"):
            raw = raw_data.get(key)
            if raw and isinstance(raw, str) and raw.startswith("{"):
                try:
                    raw_data[key] = json.loads(raw)
                except Exception:
                    pass
        return raw_data

    def parse_order(self, data: dict[str, Any]) -> dict[str, Any]:
        order_obj = data.get("order") if isinstance(data.get("order"), dict) else data
        detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        foods = (
            data.get("items")
            or detail.get("foods")
            or order_obj.get("detail")
            or []
        )
        if isinstance(foods, str):
            try:
                foods = json.loads(foods)
            except Exception:
                foods = []

        items = []
        for it in foods:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "platform_sku_id": str(it.get("sku_id") or it.get("app_food_code") or it.get("food_id") or ""),
                    "name": it.get("name") or it.get("food_name") or "外卖餐品",
                    "quantity": int(it.get("quantity") or it.get("num") or 1),
                    "price": Decimal(str(it.get("price") or it.get("food_price") or "0")),
                    "options": it.get("options") or [],
                }
            )
        return {
            "platform": self.platform,
            "platform_order_id": str(
                data.get("order_id")
                or order_obj.get("order_id")
                or data.get("wm_order_id_view")
                or order_obj.get("wm_order_id_view")
                or ""
            ),
            "platform_shop_id": str(
                data.get("app_poi_code") or data.get("wm_poi_id") or data.get("poi_id") or order_obj.get("app_poi_code") or ""
            ),
            "platform_day_seq": str(data.get("day_seq") or order_obj.get("day_seq") or data.get("daySeq") or ""),
            "store_id": int(data.get("store_id") or 0) or None,
            "total_amount": Decimal(str(data.get("total") or order_obj.get("total") or data.get("total_amount") or "0")),
            "recipient_name": data.get("recipient_name") or order_obj.get("recipient_name"),
            "recipient_phone": data.get("recipient_phone") or order_obj.get("recipient_phone"),
            "recipient_address": data.get("recipient_address") or order_obj.get("recipient_address"),
            "remark": data.get("remark") or data.get("caution") or order_obj.get("caution"),
            "packing_fee": Decimal(str(data.get("packing_fee") or order_obj.get("packing_bag") or "0")),
            "delivery_fee": Decimal(str(data.get("shipping_fee") or data.get("delivery_fee") or "0")),
            "items": items,
            "event": str(data.get("event") or data.get("status") or "ORDER_NEW"),
            "logistics_status": data.get("logistics_status") or order_obj.get("logistics_status"),
            "logistics_code": data.get("logistics_code") or order_obj.get("logistics_code"),
            "rider_name": data.get("rider_name") or data.get("courier_name"),
            "rider_phone": data.get("rider_phone") or data.get("courier_phone"),
            "refund_id": str(data.get("refund_id") or data.get("refundId") or ""),
            "refund_amount": Decimal(str(data.get("refund_amount") or data.get("refundAmount") or "0")),
            "reason": data.get("reason") or data.get("refund_reason"),
        }

    async def _request(self, path: str, biz_params: dict[str, Any], method: str = "GET") -> dict:
        settings = get_settings()
        app_id = settings.meituan_app_id
        app_secret = settings.meituan_app_secret
        if not app_id or not app_secret:
            if settings.is_production or not settings.allow_delivery_mock:
                raise ValueError("美团未配置，禁止 Mock 成功")
            logger.warning("[MEITUAN mock] %s %s", path, biz_params)
            return {"data": "ok", "_mock": True}

        if self.credentials.platform_shop_id and "app_poi_code" not in biz_params:
            biz_params = {**biz_params, "app_poi_code": self.credentials.platform_shop_id}

        base = settings.meituan_api_base.rstrip("/")
        url = f"{base}{path}"
        params = {
            "app_id": app_id,
            "timestamp": str(int(time.time())),
            **{k: str(v) for k, v in biz_params.items() if v is not None},
        }
        params["sig"] = meituan_sign(url, params, app_secret)
        client = await get_http_client()
        if method.upper() == "POST":
            resp = await client.post(url, data=params)
        else:
            resp = await client.get(url, params=params)
        # HTTP 4xx/5xx 不能仅凭 JSON body 中的 data=ok 被误判为平台成功。
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            logger.error("美团响应非 JSON: %s", resp.text[:200])
            return {"data": "ng", "error": {"msg": resp.text[:200]}}
        if data.get("data") != "ok" and data.get("error"):
            logger.error("美团 API 失败 %s: %s", path, data)
        return data

    async def confirm_order(self, platform_order_id: str) -> bool:
        # 官方: GET /api/v1/order/confirm  order_id
        data = await self._request("/api/v1/order/confirm", {"order_id": platform_order_id})
        if data.get("_mock"):
            return bool(get_settings().allow_delivery_mock)
        return data.get("data") == "ok"

    async def sync_status(self, platform_order_id: str, status: str) -> bool:
        if status == "ACCEPTED":
            return await self.confirm_order(platform_order_id)
        if status == "READY":
            data = await self._request(
                "/api/v1/order/preparationMealComplete",
                {"order_id": platform_order_id},
            )
            if data.get("_mock"):
                return bool(get_settings().allow_delivery_mock)
            return data.get("data") == "ok"
        if status in ("CANCELLED", "DONE"):
            logger.info("[MEITUAN] status %s 由平台/配送侧驱动，本地仅记录: %s", status, platform_order_id)
            return True
        return True

    async def reply_urge(self, platform_order_id: str, raw: dict[str, Any]) -> bool:
        # 官方: GET /api/v1/order/remindReply  order_id + reply_type(1=已出餐/2=正在出餐)
        data = await self._request(
            "/api/v1/order/remindReply",
            {"order_id": platform_order_id, "reply_type": raw.get("reply_type", 2)},
        )
        return _ok_or_mock(data, ok_expr=data.get("data") == "ok")

    async def agree_refund(self, refund_id: str, platform_order_id: str) -> bool:
        data = await self._request(
            "/api/v1/order/refund/agree",
            {"order_id": platform_order_id, "refund_id": refund_id},
        )
        return _ok_or_mock(data, ok_expr=data.get("data") == "ok")

    async def disagree_refund(self, refund_id: str, platform_order_id: str, reason: str) -> bool:
        data = await self._request(
            "/api/v1/order/refund/reject",
            {"order_id": platform_order_id, "refund_id": refund_id, "reason": reason},
        )
        return _ok_or_mock(data, ok_expr=data.get("data") == "ok")

    async def sync_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        food_data = json.dumps(
            {
                "app_food_code": payload["app_food_code"],
                "name": payload["name"],
                "description": payload.get("description", ""),
                "price": payload["price"],
                "box_price": payload.get("box_price", "0"),
                "min_order_count": payload.get("min_order_count", 1),
                "skus": payload.get("skus") or [{"sku_id": payload["app_food_code"], "price": payload["price"], "spec": "默认"}],
            },
            ensure_ascii=False,
        )
        data = await self._request(
            "/api/v1/food/save",
            {"app_food_code": payload["app_food_code"], "food_data": food_data},
            method="POST",
        )
        ok = _ok_or_mock(data, ok_expr=data.get("data") == "ok")
        return {"ok": ok, "platform_id": payload["app_food_code"] if ok else None, "error": None if ok else str(data.get("error"))}

    async def cancel_order(self, platform_order_id: str, reason: str = "商家取消") -> bool:
        data = await self._request(
            "/api/v1/order/cancel",
            {"order_id": platform_order_id, "reason": reason},
        )
        return _ok_or_mock(data, ok_expr=data.get("data") == "ok")

    async def sync_prices(self, items: list[dict]) -> int:
        ok = 0
        for it in items:
            data = await self._request(
                "/api/v1/food/updatePrice",
                {"app_food_code": it["app_food_code"], "price": it["price"]},
            )
            if _ok_or_mock(data, ok_expr=data.get("data") == "ok"):
                ok += 1
        return ok

    async def sync_stock(self, items: list[dict]) -> int:
        ok = 0
        for it in items:
            data = await self._request(
                "/api/v1/food/updateStock",
                {"app_food_code": it["app_food_code"], "stock": it["stock"]},
            )
            if _ok_or_mock(data, ok_expr=data.get("data") == "ok"):
                ok += 1
        return ok

    # ── 步骤2：批量 / 上下架 / 原子库存 / 门店营业状态（官方 food/batchinitdata 等）──

    async def batch_sync_products(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        if not payloads:
            return {"ok": 0, "fail": 0, "errors": []}
        food_data = [
            {
                "app_food_code": p["app_food_code"],
                "category_name": p.get("category_name") or "默认分类",
                "name": p["name"],
                "description": p.get("description", ""),
                "price": p["price"],
                "is_sold_out": 0,
                "min_order_count": p.get("min_order_count", 1) or 1,
                "unit": "份",
                "box_price": str(p.get("box_price", 0) or 0),
                "picture": p.get("picture", ""),
                "skus": p.get("skus")
                or [
                    {
                        "sku_id": p["app_food_code"],
                        "spec": "默认",
                        "price": p["price"],
                        "stock": p.get("stock", 9999),
                    }
                ],
            }
            for p in payloads
        ]
        data = await self._request(
            "/api/v1/food/batchinitdata",
            {"food_data": json.dumps(food_data, ensure_ascii=False)},
            method="POST",
        )
        if _ok_or_mock(data, ok_expr=data.get("data") == "ok"):
            return {"ok": len(food_data), "fail": 0, "errors": []}
        return {"ok": 0, "fail": len(food_data), "errors": [str(data.get("error"))]}

    async def set_sell_status(self, items: list[dict[str, Any]], on_shelf: bool) -> int:
        # 官方 food/sku/sellStatus：sell_status 1=下架，0=上架
        food_data = [
            {
                "app_food_code": it["app_food_code"],
                "skus": [{"sku_id": it.get("platform_sku_id") or it["app_food_code"]}],
            }
            for it in items
        ]
        data = await self._request(
            "/api/v1/food/sku/sellStatus",
            {
                "sell_status": 0 if on_shelf else 1,
                "food_data": json.dumps(food_data, ensure_ascii=False),
            },
            method="POST",
        )
        return len(items) if _ok_or_mock(data, ok_expr=data.get("data") == "ok") else 0

    async def set_sku_stock(self, items: list[dict[str, Any]]) -> int:
        # 售罄置0 / 定时对账主路径：绝对覆盖，复用已验证的 updateStock
        return await self.sync_stock(items)

    async def _adjust_stock(self, items: list[dict[str, Any]], path: str) -> int:
        # 原子增减（预留）。注意：官方 inc_stock/desc_stock 数量字段名以真机文档为准，此处用 count。
        ok = 0
        for it in items:
            qty = int(it.get("qty") or it.get("stock") or 0)
            if qty <= 0:
                continue
            data = await self._request(
                path,
                {
                    "app_food_code": it["app_food_code"],
                    "sku_id": it.get("platform_sku_id") or it["app_food_code"],
                    "count": qty,
                },
            )
            if _ok_or_mock(data, ok_expr=data.get("data") == "ok"):
                ok += 1
        return ok

    async def inc_stock(self, items: list[dict[str, Any]]) -> int:
        return await self._adjust_stock(items, "/api/v1/food/sku/inc_stock")

    async def dec_stock(self, items: list[dict[str, Any]]) -> int:
        return await self._adjust_stock(items, "/api/v1/food/sku/desc_stock")

    async def set_store_open_status(self, open_status: str, shipping_time: str | None = None) -> bool:
        # OPEN→poi/online；OFFLINE→poi/offline（只需 app_poi_code，_request 自动注入）。
        # REST→poi/save open_level=3；真实环境 poi/save 为全量字段，必要时先 poi/mget 回写（联调点）。
        if open_status == "OPEN":
            data = await self._request("/api/v1/poi/online", {}, method="POST")
        elif open_status == "OFFLINE":
            data = await self._request("/api/v1/poi/offline", {}, method="POST")
        else:
            biz: dict[str, Any] = {"open_level": 3}
            if shipping_time:
                biz["shipping_time"] = shipping_time
            data = await self._request("/api/v1/poi/save", biz, method="POST")
        return _ok_or_mock(data, ok_expr=data.get("data") == "ok")


class ElemeProvider(DeliveryProvider):
    """饿了么商家开放平台 open-api.shop.ele.me（JSON-RPC）"""

    platform = "ELEME"

    def verify_notify(self, headers: dict, body: bytes, notify_url: str = "") -> dict[str, Any]:
        settings = get_settings()
        text = body.decode("utf-8")
        data = json.loads(text) if text.strip().startswith("{") else flatten_form_body(body)

        if data.get("_dev_simulate") and _dev_mode_allowed():
            logger.warning("饿了么开发模式：跳过验签")
            return data

        if not settings.eleme_app_secret:
            raise ValueError("饿了么未配置，拒绝回调")

        sig = data.get("signature") or headers.get("x-eleme-signature")
        if not sig:
            raise ValueError("饿了么回调缺少 signature，拒绝")
        if not settings.eleme_access_token:
            raise ValueError("饿了么 access_token 未配置，无法验签")
        action = str(data.get("type") or data.get("action") or "push")
        expected = eleme_shop_sign(
            action,
            settings.eleme_access_token,
            {"app_key": settings.eleme_app_id, "timestamp": data.get("timestamp", "")},
            {"message": data.get("message", "")},
            settings.eleme_app_secret,
        )
        if str(sig).upper() != expected:
            raise ValueError("饿了么推送签名验证失败")
        return data

    def parse_order(self, data: dict[str, Any]) -> dict[str, Any]:
        message = data.get("message") or data
        if isinstance(message, str):
            try:
                message = json.loads(message)
            except Exception:
                message = {}
        order = message.get("order") or message
        groups = order.get("groups") or order.get("items") or []
        items = []
        for g in groups:
            for it in g.get("items") if isinstance(g, dict) and "items" in g else [g]:
                if not isinstance(it, dict):
                    continue
                items.append(
                    {
                        "platform_sku_id": str(it.get("vfoodId") or it.get("skuId") or it.get("food_id") or ""),
                        "name": it.get("name") or "外卖餐品",
                        "quantity": int(it.get("quantity") or 1),
                        "price": Decimal(str(it.get("price") or "0")),
                        "options": it.get("newSpecs") or it.get("options") or [],
                    }
                )
        event = str(data.get("type") or data.get("event") or "ORDER_NEW")
        # type=217 新订单；含 cancel 视为取消
        if "cancel" in event.lower() or event in ("14", "15", "17"):
            event = "CANCEL"
        return {
            "platform": self.platform,
            "platform_order_id": str(order.get("id") or order.get("orderId") or data.get("order_id") or ""),
            "platform_shop_id": str(order.get("shopId") or data.get("shopId") or data.get("shop_id") or ""),
            "platform_day_seq": str(order.get("daySn") or order.get("day_seq") or ""),
            "store_id": None,  # 必须通过 platform_store_bindings 解析，禁止 shopId 当本地 store_id
            "total_amount": Decimal(str(order.get("totalPrice") or order.get("total_amount") or "0")),
            "recipient_name": (order.get("consignee") or {}).get("name")
            if isinstance(order.get("consignee"), dict)
            else order.get("recipient_name"),
            "recipient_phone": (order.get("consignee") or {}).get("phone")
            if isinstance(order.get("consignee"), dict)
            else order.get("recipient_phone"),
            "recipient_address": order.get("deliveryPoiAddress") or order.get("address") or order.get("recipient_address"),
            "remark": order.get("description") or order.get("remark"),
            "packing_fee": Decimal(str(order.get("packageFee") or "0")),
            "delivery_fee": Decimal(str(order.get("deliverFee") or "0")),
            "items": items,
            "event": event,
            "logistics_status": order.get("logisticsStatus") or data.get("logistics_status"),
            "logistics_code": order.get("logisticsCode") or data.get("logistics_code"),
            "rider_name": (order.get("deliveryMan") or {}).get("name") if isinstance(order.get("deliveryMan"), dict) else None,
            "rider_phone": (order.get("deliveryMan") or {}).get("phone") if isinstance(order.get("deliveryMan"), dict) else None,
            "refund_id": str(data.get("refundId") or order.get("refundId") or ""),
            "refund_amount": Decimal(str(order.get("refundAmount") or data.get("refund_amount") or "0")),
            "reason": order.get("refundReason") or data.get("reason"),
        }

    async def _rpc(self, action: str, params: dict[str, Any]) -> dict:
        settings = get_settings()
        token = self.credentials.access_token or settings.eleme_access_token
        if not settings.eleme_app_id or not settings.eleme_app_secret or not token:
            if settings.is_production or not settings.allow_delivery_mock:
                raise ValueError("饿了么未配置，禁止 Mock 成功")
            logger.warning("[ELEME mock] %s %s", action, params)
            return {"result": "ok", "_mock": True}

        metas = {
            "app_key": settings.eleme_app_id,
            "timestamp": int(time.time()),
        }
        payload = {
            "nop": "1.0.0",
            "id": str(uuid.uuid4()),
            "action": action,
            "token": token,
            "metas": metas,
            "params": params,
            "signature": eleme_shop_sign(action, token, metas, params, settings.eleme_app_secret),
        }
        url = settings.eleme_api_base.rstrip("/") + "/"
        client = await get_http_client()
        resp = await client.post(url, json=payload)
        # JSON-RPC 错误体可能不含 error 字段；HTTP 非 2xx 必须先失败。
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            logger.error("饿了么响应非 JSON: %s", resp.text[:200])
            return {"error": resp.text[:200]}
        if data.get("error"):
            logger.error("饿了么 API 失败 %s: %s", action, data)
        return data

    async def confirm_order(self, platform_order_id: str) -> bool:
        # 官方推荐: eleme.order.confirmOrderLite
        data = await self._rpc("eleme.order.confirmOrderLite", {"orderId": platform_order_id})
        return _ok_or_mock(data, ok_expr=data.get("error") is None)

    async def sync_status(self, platform_order_id: str, status: str) -> bool:
        if status == "ACCEPTED":
            return await self.confirm_order(platform_order_id)
        if status == "READY":
            # 官方: eleme.order.setOrderPrepared 商家确认已完成出餐
            data = await self._rpc("eleme.order.setOrderPrepared", {"orderId": platform_order_id})
            return _ok_or_mock(data, ok_expr=data.get("error") is None)
        if status in ("CANCELLED", "DONE"):
            logger.info("[ELEME] status %s 本地记录: %s", status, platform_order_id)
            return True
        return True

    async def reply_urge(self, platform_order_id: str, raw: dict[str, Any]) -> bool:
        data = await self._rpc("eleme.order.replyReminder", {"orderId": platform_order_id})
        return _ok_or_mock(data, ok_expr=data.get("error") is None)

    async def agree_refund(self, refund_id: str, platform_order_id: str) -> bool:
        data = await self._rpc(
            "eleme.order.agreeRefundLite",
            {"orderId": platform_order_id, "refundOrderId": refund_id},
        )
        return _ok_or_mock(data, ok_expr=data.get("error") is None)

    async def disagree_refund(self, refund_id: str, platform_order_id: str, reason: str) -> bool:
        data = await self._rpc(
            "eleme.order.disagreeRefundLite",
            {"orderId": platform_order_id, "refundOrderId": refund_id, "reason": reason},
        )
        return _ok_or_mock(data, ok_expr=data.get("error") is None)

    async def sync_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.eleme_app_id:
            if settings.is_production or not settings.allow_delivery_mock:
                raise ValueError("饿了么未配置，禁止 Mock 同步餐品")
            return {"ok": True, "platform_id": None}
        data = await self._rpc(
            "eleme.product.item.createItem",
            {
                "shopId": payload["shop_id"],
                "name": payload["name"],
                "description": payload.get("description", ""),
                "categoryName": payload.get("category_name", "默认"),
                "specs": [{"specId": payload["app_food_code"], "name": "默认", "price": payload["price"]}],
            },
        )
        ok = _ok_or_mock(data, ok_expr=data.get("error") is None)
        # 饿了么 createItem 返回 result.itemId（数字字符串），保存后才能后续 updateItem
        result = data.get("result") or {}
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {}
        platform_id = str(result.get("itemId") or result.get("id") or "") if ok else None
        return {"ok": ok, "platform_id": platform_id or None, "error": None if ok else str(data.get("error"))}

    async def sync_prices(self, items: list[dict]) -> int:
        ok = 0
        for it in items:
            data = await self._rpc(
                "eleme.product.item.updateItem",
                {"itemId": it.get("platform_sku_id"), "specs": [{"price": it["price"]}]},
            )
            if _ok_or_mock(data, ok_expr=data.get("error") is None):
                ok += 1
        return ok

    async def sync_stock(self, items: list[dict]) -> int:
        ok = 0
        for it in items:
            data = await self._rpc(
                "eleme.product.item.updateItem",
                {"itemId": it.get("platform_sku_id"), "stock": it["stock"]},
            )
            if _ok_or_mock(data, ok_expr=data.get("error") is None):
                ok += 1
        return ok

    async def cancel_order(self, platform_order_id: str, reason: str = "商家取消") -> bool:
        data = await self._rpc(
            "eleme.order.cancelOrderLite",
            {"orderId": platform_order_id, "reason": reason, "type": "restaurantClose"},
        )
        return _ok_or_mock(data, ok_expr=data.get("error") is None)

    # ── 步骤2：批量 / 上下架 / 库存 / 门店开关 ──

    async def batch_sync_products(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        # 饿了么餐饮无批量建档接口，逐个 createItem，聚合结果
        ok = 0
        errors: list[str] = []
        for p in payloads:
            res = await self.sync_product(p)
            if res.get("ok"):
                ok += 1
            else:
                errors.append(str(res.get("error")))
        return {"ok": ok, "fail": len(payloads) - ok, "errors": errors}

    async def set_sell_status(self, items: list[dict[str, Any]], on_shelf: bool) -> int:
        action = "eleme.product.item.onShelf" if on_shelf else "eleme.product.item.offShelf"
        ok = 0
        for it in items:
            data = await self._rpc(action, {"itemId": it.get("platform_sku_id") or it.get("app_food_code")})
            if _ok_or_mock(data, ok_expr=data.get("error") is None):
                ok += 1
        return ok

    async def set_sku_stock(self, items: list[dict[str, Any]]) -> int:
        # 饿了么仅支持绝对库存覆盖
        return await self.sync_stock(items)

    async def inc_stock(self, items: list[dict[str, Any]]) -> int:
        logger.warning("饿了么不支持原子增减库存，统一走 set_sku_stock 绝对覆盖")
        return 0

    async def dec_stock(self, items: list[dict[str, Any]]) -> int:
        logger.warning("饿了么不支持原子增减库存，统一走 set_sku_stock 绝对覆盖")
        return 0

    async def set_store_open_status(self, open_status: str, shipping_time: str | None = None) -> bool:
        # 饿了么餐饮：openShop / closeShop（休息与下线均为闭店）。
        # 营业时间需通过 eleme.shop.updateShop 维护，字段随门店类型不同（联调点），此处仅在开店时附带日志。
        shop_id = self.credentials.platform_shop_id
        if open_status == "OPEN":
            data = await self._rpc("eleme.shop.openShop", {"shopId": shop_id})
        else:
            if shipping_time:
                logger.info("[ELEME] 营业时间更新需 updateShop 联调，shipping_time=%s", shipping_time)
            data = await self._rpc("eleme.shop.closeShop", {"shopId": shop_id})
        return _ok_or_mock(data, ok_expr=data.get("error") is None)


PROVIDERS: dict[str, type[DeliveryProvider]] = {
    "MEITUAN": MeituanProvider,
    "ELEME": ElemeProvider,
}


def get_delivery_provider(platform: str, credentials: PlatformCredentials | None = None) -> DeliveryProvider:
    cls = PROVIDERS.get(platform.upper())
    if not cls:
        raise ValueError(f"不支持的外卖平台: {platform}")
    return cls(credentials)
