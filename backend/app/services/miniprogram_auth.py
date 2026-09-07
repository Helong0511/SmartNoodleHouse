"""微信/支付宝小程序登录（code2session）—— 生产禁止 mock"""
import hashlib
import logging
from datetime import datetime

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.utils.alipay_keys import alipay_gateway_request, sign_alipay_params_rsa2

logger = logging.getLogger(__name__)


def _mock_allowed(*, provider_configured: bool = False) -> bool:
    """生产禁止 mock；开发未配密钥或显式 ALLOW_AUTH_MOCK=true 时允许。"""
    s = get_settings()
    if s.is_production:
        return False
    if s.allow_auth_mock:
        return True
    # 本地调试：未配置小程序密钥时自动走 mock，避免必配微信才能进首页
    return s.is_development and not provider_configured


def _allow_explicit_mock_code(code: str) -> bool:
    """mock: 前缀为开发者显式信号，非生产环境一律走 mock（即使已配置真实密钥）。"""
    if not code or not str(code).startswith("mock:"):
        return False
    return not get_settings().is_production


def _mock_wechat_openid(code: str) -> dict:
    """开发 mock：禁止用一次性 code 当 openid，同一设备身份须稳定。"""
    if code.startswith("mock:") and len(code) > 5:
        device = code[5:].strip()[:32] or "simulator"
        openid = "wx_dev_" + hashlib.md5(device.encode()).hexdigest()[:16]
    else:
        openid = "wx_dev_simulator"
    logger.warning("WECHAT 开发 Mock openid=%s（稳定身份，不会因 code 变化新建顾客）", openid)
    return {"openid": openid, "unionid": None, "session_key": "dev_session", "is_mock": True}


def _mock_alipay_user_id(auth_code: str) -> dict:
    if auth_code.startswith("mock:") and len(auth_code) > 5:
        device = auth_code[5:].strip()[:32] or "simulator"
        user_id = "ali_dev_" + hashlib.md5(device.encode()).hexdigest()[:16]
    else:
        user_id = "ali_dev_simulator"
    logger.warning("ALIPAY 开发 Mock user_id=%s（稳定身份）", user_id)
    return {"alipay_user_id": user_id, "is_mock": True}


class WechatMiniAuth:
    async def code2session(self, code: str) -> dict:
        settings = get_settings()
        if _allow_explicit_mock_code(code):
            return _mock_wechat_openid(code)
        configured = bool(settings.wechat_app_id and settings.wechat_app_secret)
        if not configured:
            if not _mock_allowed(provider_configured=False):
                raise AppException(50030, "微信小程序未配置，拒绝登录（开发可设 ALLOW_AUTH_MOCK=true）")
            return _mock_wechat_openid(code)

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
        if _allow_explicit_mock_code(auth_code):
            return _mock_alipay_user_id(auth_code)
        configured = bool(settings.alipay_app_id and settings.alipay_private_key)
        if not configured:
            if not _mock_allowed(provider_configured=False):
                raise AppException(50032, "支付宝小程序未配置，拒绝登录（开发可设 ALLOW_AUTH_MOCK=true）")
            return _mock_alipay_user_id(auth_code)

        params = {
            "app_id": settings.alipay_app_id,
            "method": "alipay.system.oauth.token",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "grant_type": "authorization_code",
            "code": auth_code,
        }
        try:
            params["sign"] = sign_alipay_params_rsa2(params, settings.alipay_private_key)
            data = await alipay_gateway_request(params, timeout=10.0)
            token_resp = data.get("alipay_system_oauth_token_response") or {}
            # 错误体也可能在 error_response
            if not token_resp and data.get("error_response"):
                err = data["error_response"]
                raise AppException(50033, err.get("sub_msg") or err.get("msg") or "支付宝 OAuth 失败")
            if token_resp.get("code") and token_resp.get("code") != "10000":
                raise AppException(50033, token_resp.get("sub_msg") or token_resp.get("msg") or "支付宝 OAuth 失败")
            open_id = token_resp.get("open_id")
            legacy_user_id = token_resp.get("user_id")
            # 同时返回时优先 open_id，避免同一账号在 user_id/open_id 间切换重复建客
            if open_id and legacy_user_id and open_id != legacy_user_id:
                logger.info(
                    "支付宝 OAuth 同时返回 open_id 与 user_id，采用 open_id 作为稳定身份 open_id=%s user_id=%s",
                    open_id,
                    legacy_user_id,
                )
            user_id = open_id or legacy_user_id
            if not user_id:
                # 便于排查：常见是签名错误、app_id 不匹配
                preview = {k: data.get(k) for k in ("error_response", "alipay_system_oauth_token_response") if k in data}
                logger.error("支付宝 OAuth 无 user_id，响应摘要=%s", preview or list(data.keys())[:8])
                raise AppException(50033, "支付宝未返回用户标识（请核对 ALIPAY_APP_ID / 应用私钥是否与开放平台一致）")
            return {"alipay_user_id": user_id, "is_mock": False}
        except AppException:
            raise
        except Exception as e:
            logger.error("支付宝 authCode 换用户失败: %s", e)
            raise AppException(50033, f"支付宝登录失败: {e}") from e
