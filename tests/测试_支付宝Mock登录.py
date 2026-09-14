"""mock: 前缀登录：非生产环境即使已配置真实密钥也应走开发身份。"""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.config import get_settings
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth


async def main() -> None:
    settings = get_settings()
    if settings.is_production:
        print("SKIP: production 环境不测试 mock")
        return

    ali = AlipayMiniAuth()
    session = await ali.auth_code2user("mock:simulator")
    assert session.get("is_mock") is True
    assert str(session["alipay_user_id"]).startswith("ali_dev_")
    print("OK alipay mock:simulator ->", session["alipay_user_id"])

    wx = WechatMiniAuth()
    wx_session = await wx.code2session("mock:simulator")
    assert wx_session.get("is_mock") is True
    assert str(wx_session["openid"]).startswith("wx_dev_")
    print("OK wechat mock:simulator ->", wx_session["openid"])

    # 已配置密钥时 mock: 仍应优先（IDE getAuthCode 失败场景）
    with patch(
        "app.services.miniprogram_auth.get_settings",
        return_value=type(
            "S",
            (),
            {
                "is_production": False,
                "allow_auth_mock": False,
                "is_development": True,
                "wechat_app_id": "wx_test",
                "wechat_app_secret": "secret",
                "alipay_app_id": "ali_test",
                "alipay_private_key": "key",
                "alipay_gateway": "https://openapi.alipay.com/gateway.do",
            },
        )(),
    ):
        ali2 = AlipayMiniAuth()
        s2 = await ali2.auth_code2user("mock:simulator")
        assert s2.get("is_mock") is True
        wx2 = WechatMiniAuth()
        s3 = await wx2.code2session("mock:simulator")
        assert s3.get("is_mock") is True
    print("OK mock: prefix bypasses configured provider credentials")


if __name__ == "__main__":
    asyncio.run(main())
