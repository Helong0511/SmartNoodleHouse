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


def _mock_allowed(*, provider_configured: bool = False) -> bool:
    """生产禁止 mock；开发未配密钥或显式 ALLOW_AUTH_MOCK=true 时允许。"""
    s = get_settings()
    if s.is_production:
        return False
    if s.allow_auth_mock:
        return True
    # 本地调试：未配置小程序密钥时自动走 mock，避免必配微信才能进首页
    return s.is_development and not provider_configured


class WechatMiniAuth:
    async def code2session(self, code: str) -> dict:
        settings = get_settings()
        configured = bool(settings.wechat_app_id and settings.wechat_app_secret)
        if not configured:
            if not _mock_allowed(provider_configured=False):
                raise AppException(50030, "微信小程序未配置，拒绝登录（开发可设 ALLOW_AUTH_MOCK=true）")
            # 禁止用一次性 code 生成 openid：每次 wx.login 的 code 都不同，会不断新建顾客。
            # 真实微信同一用户 openid 稳定；本地 mock 固定为 wx_dev_simulator。
            # 多账号调试可在 code 前缀传 mock:<device_id>（开发者工具可改 auth.js）
            if code.startswith("mock:") and len(code) > 5:
                device = code[5:].strip()[:32] or "simulator"
                openid = "wx_dev_" + hashlib.md5(device.encode()).hexdigest()[:16]
            else:
                openid = "wx_dev_simulator"
            logger.warning("WECHAT 未配置，开发 Mock openid=%s（稳定身份，不会因 code 变化新建顾客）", openid)
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
            errmsg = data.get("errmsg") or str(data.get("errcode"))
            hint = ""
            if data.get("errcode") in (40013, 40125, 40001, 40163, 40029):
                hint = "（请确认 .env 的 WECHAT_APP_ID/SECRET 与小程序 project.config.json 的 appid 一致）"
            raise AppException(50031, f"微信登录失败: {errmsg}{hint}")
        return {
            "openid": data["openid"],
            "unionid": data.get("unionid"),
            "session_key": data.get("session_key"),
            "is_mock": False,
        }


class AlipayMiniAuth:
    async def auth_code2user(self, auth_code: str) -> dict:
        settings = get_settings()
        configured = bool(settings.alipay_app_id and settings.alipay_private_key)
        if not configured:
            if not _mock_allowed(provider_configured=False):
                raise AppException(50032, "支付宝小程序未配置，拒绝登录（开发可设 ALLOW_AUTH_MOCK=true）")
            # 同微信：authCode 一次性，不可用来生成稳定用户身份
            if auth_code.startswith("mock:") and len(auth_code) > 5:
                device = auth_code[5:].strip()[:32] or "simulator"
                user_id = "ali_dev_" + hashlib.md5(device.encode()).hexdigest()[:16]
            else:
                user_id = "ali_dev_simulator"
            logger.warning("ALIPAY 未配置，开发 Mock user_id=%s（稳定身份）", user_id)
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
