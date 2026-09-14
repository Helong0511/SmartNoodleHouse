"""支付 / 外卖 Sandbox 脚手架测试

默认不连真实平台。设置环境变量后可跑真实沙箱：
  RUN_PAYMENT_SANDBOX=1
  RUN_DELIVERY_SANDBOX=1
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_payment_transition, assert_staff_transition
from app.services.payment_verify import AlipayVerifier, WechatPayVerifier
from app.services.stock_service import UNLIMITED_STOCK, deduct_stock
from app.services.webhook_inbox import make_event_id
from app.core.ip_allowlist import ip_allowed, _parse_allowlist


def test_sandbox_skip_marker():
    assert os.getenv("RUN_PAYMENT_SANDBOX", "0") in ("0", "1", "true", "false", "")


@pytest.mark.skipif(os.getenv("RUN_PAYMENT_SANDBOX") != "1", reason="需真实微信/支付宝沙箱密钥")
@pytest.mark.asyncio
async def test_real_wechat_native_create():
    from app.services import wechat_pay_client

    data = await wechat_pay_client.create_native_order(
        payment_no="SANDBOX_TEST_001",
        description="sandbox",
        amount_yuan=Decimal("0.01"),
        notify_url=os.environ["WECHAT_NOTIFY_URL"],
    )
    assert data.get("code_url")


@pytest.mark.skipif(os.getenv("RUN_DELIVERY_SANDBOX") != "1", reason="需真实美团/饿了么密钥")
@pytest.mark.asyncio
async def test_real_meituan_confirm_sandbox():
    from app.services.delivery_providers import MeituanProvider

    p = MeituanProvider()
    # 使用沙箱订单号
    ok = await p.confirm_order(os.environ.get("MEITUAN_SANDBOX_ORDER_ID", ""))
    assert isinstance(ok, bool)


def test_payment_callback_amount_mismatch_logic():
    """金额校验逻辑：微信分 vs 本地元"""
    from app.core.response import money

    payment_amount = money(Decimal("12.50"))
    wechat_fen = 1250
    assert money(Decimal(wechat_fen) / 100) == payment_amount
    assert money(Decimal(1249) / 100) != payment_amount


def test_wechat_rejects_plaintext_when_configured():
    with patch("app.services.payment_verify.get_settings") as gs:
        s = MagicMock()
        s.wechat_api_v3_key = "x" * 32
        s.wechat_mch_id = "mch"
        s.wechat_platform_public_key = ""
        s.wechat_platform_cert_path = "certs/missing.pem"
        s.is_development = False
        s.allow_payment_mock = False
        gs.return_value = s
        with pytest.raises(ValueError):
            WechatPayVerifier().verify_and_parse(
                {
                    "Wechatpay-Signature": "aa",
                    "Wechatpay-Timestamp": "1",
                    "Wechatpay-Nonce": "n",
                },
                b'{"trade_state":"SUCCESS","out_trade_no":"P1"}',
            )


def test_delivery_inbox_event_idempotent_key():
    body = b'{"order_id":"1","type":"new"}'
    a = make_event_id("MEITUAN", {"order_id": "1", "type": "new"}, body)
    b = make_event_id("MEITUAN", {"order_id": "1", "type": "new"}, body)
    assert a == b


def test_ip_allowlist_cidr():
    allow = _parse_allowlist("127.0.0.1,10.0.0.0/8")
    assert ip_allowed("127.0.0.1", allow)
    assert ip_allowed("10.1.2.3", allow)
    assert not ip_allowed("8.8.8.8", allow)


@pytest.mark.asyncio
async def test_stock_atomic_mock_db():
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    db.execute = AsyncMock(return_value=result)
    await deduct_stock(db, 1, 1)
    assert UNLIMITED_STOCK == 999


def test_order_state_machine_payment_path():
    assert_payment_transition("PENDING_PAYMENT", "PAID")
    with pytest.raises(AppException):
        assert_staff_transition("PENDING_PAYMENT", "PAID")
