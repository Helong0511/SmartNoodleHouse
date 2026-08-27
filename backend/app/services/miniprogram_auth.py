"""微信/支付宝小程序登录（code2session）—— 生产禁止 mock"""
import base64
import hashlib
import logging
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


def _load_private_key(key_str: str):
    from cryptography.hazmat.backends import default_backend

    key_str = key_str.strip()
    if not key_str.startswith("-----") and "BEGIN" not in key_str:
        key_str = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + "\n".join(key_str[i : i + 64] for i in range(0, len(key_str), 64))
            + "\n-----END RSA PRIVATE KEY-----"
        )
    return serialization.load_pem_private_key(key_str.encode(), password=None, backend=default_backend())


def _sign_alipay_rsa2(params: dict, private_key_pem: str) -> str:
    items = sorted((k, v) for k, v in params.items() if v is not None and v != "" and k not in ("sign", "sign_type"))
    content = "&".join(f"{k}={v}" for k, v in items)
    private_key = _load_private_key(private_key_pem)
    signature = private_key.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")


def _mock_allowed() -> bool:
    s = get_settings()
    return s.is_development and s.allow_auth_mock


class WechatMiniAuth:
    async def code2session(self, code: str) -> dict:
        settings = get_settings()
        if not settings.wechat_app_id or not settings.wechat_app_secret:
            if not _mock_allowed():
                raise AppException(50030, "微信小程序未配置，拒绝登录")
            openid = "wx_dev_" + hashlib.md5(code.encode()).hexdigest()[:16]
            logger.warning("WECHAT 未配置，开发 Mock openid=%s", openid)
            return {"openid": openid, "unionid": None, "session_key": "dev_session", "is_mock": True}

        url = "https://api.weixin.qq.com/sns/jscode2session"
        params = {
            "appid": settings.wechat_app_id,
            "secret": settings.wechat_app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            data = resp.json()
        if data.get("errcode"):
            logger.error("微信 code2session 失败: %s", data)
            raise AppException(50031, f"微信登录失败: {data.get('errmsg') or data.get('errcode')}")
        return {
            "openid": data["openid"],
            "unionid": data.get("unionid"),
            "session_key": data.get("session_key"),
            "is_mock": False,
        }


class AlipayMiniAuth:
    async def auth_code2user(self, auth_code: str) -> dict:
        settings = get_settings()
        if not settings.alipay_app_id or not settings.alipay_private_key:
            if not _mock_allowed():
                raise AppException(50032, "支付宝小程序未配置，拒绝登录")
            user_id = "ali_dev_" + hashlib.md5(auth_code.encode()).hexdigest()[:16]
            logger.warning("ALIPAY 未配置，开发 Mock user_id=%s", user_id)
            return {"alipay_user_id": user_id, "is_mock": True}

        params = {
            "app_id": settings.alipay_app_id,
            "method": "alipay.system.oauth.token",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "grant_type": "authorization_code",
            "code": auth_code,
        }
        try:
            params["sign"] = _sign_alipay_rsa2(params, settings.alipay_private_key)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.alipay_gateway, data=params)
                data = resp.json()
            token_resp = data.get("alipay_system_oauth_token_response") or {}
            if token_resp.get("code") and token_resp.get("code") != "10000":
                raise AppException(50033, token_resp.get("sub_msg") or token_resp.get("msg") or "支付宝 OAuth 失败")
            user_id = token_resp.get("user_id") or token_resp.get("open_id")
            if not user_id:
                raise AppException(50033, "支付宝未返回用户标识")
            return {"alipay_user_id": user_id, "is_mock": False}
        except AppException:
            raise
        except Exception as e:
            logger.error("支付宝 authCode 换用户失败: %s", e)
            raise AppException(50033, f"支付宝登录失败: {e}") from e
