"""支付宝 mock 登录：已配置密钥时 mock: 前缀仍应走开发身份（IDE getAuthCode 失败场景）。"""
import asyncio
import os
import sys

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


if __name__ == "__main__":
    asyncio.run(main())
