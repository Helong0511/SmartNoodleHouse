"""微信小程序服务端能力：access_token、手机号、订阅消息。"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _mock_allowed() -> bool:
    s = get_settings()
    if s.is_production:
        return False
    return s.is_development or s.allow_auth_mock


async def get_access_token(*, force: bool = False) -> str:
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            return "mock_access_token"
        raise AppException(50030, "微信小程序未配置，无法获取 access_token")

    now = time.time()
    if not force and _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": settings.wechat_app_id,
        "secret": settings.wechat_app_secret,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
    if data.get("errcode"):
        logger.error("微信 access_token 失败: %s", data)
        raise AppException(50032, f"获取微信 access_token 失败: {data.get('errmsg', data.get('errcode'))}")
    token = data.get("access_token") or ""
    if not token:
        raise AppException(50032, "微信未返回 access_token")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in") or 7200)
    return token


async def get_phone_by_code(code: str) -> str:
    """getPhoneNumber 动态令牌换手机号。"""
    code = (code or "").strip()
    if not code:
        raise AppException(40054, "缺少 phone_code")

    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            digest = hashlib.md5(code.encode()).hexdigest()[:8]
            return f"138{digest[:8]}".ljust(11, "0")[:11]
        raise AppException(50030, "微信小程序未配置，无法解析手机号")

    token = await get_access_token()
    url = f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={"code": code})
        data = resp.json()
    if data.get("errcode"):
        if data.get("errcode") in (40001, 42001):
            token = await get_access_token(force=True)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}",
                    json={"code": code},
                )
                data = resp.json()
        if data.get("errcode"):
            logger.error("微信 getPhoneNumber 失败: %s", data)
            raise AppException(50033, f"获取手机号失败: {data.get('errmsg', data.get('errcode'))}")
    info = data.get("phone_info") or {}
    phone = (info.get("purePhoneNumber") or info.get("phoneNumber") or "").strip()
    if not phone:
        raise AppException(50033, "微信未返回手机号")
    return phone


async def send_order_ready_subscribe(
    *,
    openid: str,
    order_no: str,
    order_id: int,
    table_label: str | None = None,
) -> bool:
    """订单 READY 时发送订阅消息（未配置模板则跳过）。"""
    settings = get_settings()
    tmpl = (settings.wechat_subscribe_template_order_ready or "").strip()
    if not tmpl or not openid:
        return False
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            logger.info("Mock 订阅消息 order_ready openid=%s order=%s", openid, order_no)
            return True
        return False

    token = await get_access_token()
    url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
    table_text = (table_label or "堂食").strip()[:20] or "堂食"
    payload = {
        "touser": openid,
        "template_id": tmpl,
        "page": f"packageOrder/result/result?order_id={order_id}",
        "miniprogram_state": "formal",
        "lang": "zh_CN",
        "data": {
            # 请在微信公众平台选用含以下关键字的模板，或按需调整字段名
            "character_string1": {"value": (order_no or "-")[:32]},
            "thing2": {"value": "餐品已备好，请取餐"},
            "thing3": {"value": table_text},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
        if data.get("errcode") == 0:
            return True
        logger.warning("订阅消息发送失败 order=%s err=%s", order_no, data)
    except Exception as e:
        logger.warning("订阅消息异常 order=%s: %s", order_no, e)
    return False
