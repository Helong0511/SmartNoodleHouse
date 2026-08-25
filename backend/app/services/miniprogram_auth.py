"""微信/支付宝小程序登录（code2session）"""
import base64
import hashlib
import logging
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.config import get_settings

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


class WechatMiniAuth:
    """微信小程序 code2session"""

    async def code2session(self, code: str) -> dict:
        settings = get_settings()
        if not settings.wechat_app_id or not settings.wechat_app_secret:
            openid = "wx_dev_" + hashlib.md5(code.encode()).hexdigest()[:16]
            logger.warning("WECHAT_APP_ID 未配置，使用开发模式 openid=%s", openid)
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
            openid = "wx_err_" + hashlib.md5(code.encode()).hexdigest()[:16]
            return {"openid": openid, "unionid": None, "session_key": "", "is_mock": True}
        return {
            "openid": data["openid"],
            "unionid": data.get("unionid"),
            "session_key": data.get("session_key"),
            "is_mock": False,
        }


class AlipayMiniAuth:
    """支付宝小程序 authCode 换用户（RSA2 签名）"""

    async def auth_code2user(self, auth_code: str) -> dict:
        settings = get_settings()
        if not settings.alipay_app_id or not settings.alipay_private_key:
            user_id = "ali_dev_" + hashlib.md5(auth_code.encode()).hexdigest()[:16]
            logger.warning("ALIPAY_APP_ID 未配置，使用开发模式 user_id=%s", user_id)
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
                logger.error("支付宝 OAuth 失败: %s", token_resp)
                raise ValueError(token_resp.get("sub_msg") or token_resp.get("msg"))
            user_id = token_resp.get("user_id") or token_resp.get("open_id")
            if user_id:
                return {"alipay_user_id": user_id, "is_mock": False}
        except Exception as e:
            logger.error("支付宝 authCode 换用户失败: %s", e)

        user_id = "ali_err_" + hashlib.md5(auth_code.encode()).hexdigest()[:16]
        return {"alipay_user_id": user_id, "is_mock": True}
