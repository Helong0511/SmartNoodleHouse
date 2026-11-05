"""支付验签 / Webhook Inbox / 库存同步映射 单元测试"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.order_state_machine import assert_payment_transition, assert_staff_transition
from app.services.product_sync_service import ProductSyncService
from app.services.webhook_inbox import make_event_id


def test_payment_transition_only_pending():
    assert_payment_transition("PENDING_PAYMENT", "PAID")
    with pytest.raises(AppException):
        assert_payment_transition("PAID", "PAID")


def test_staff_cannot_jump_to_paid():
    with pytest.raises(AppException) as e:
        assert_staff_transition("CONFIRMED", "PAID")
    assert e.value.code == 40020


def test_wechat_verifier_rejects_missing_config(monkeypatch):
    from app.core.config import get_settings
    from app.services.payment_verify import WechatPayVerifier

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_PAYMENT_MOCK", "false")
    get_settings.cache_clear()
    # 直接构造：未配置密钥应拒绝
    with patch("app.services.payment_verify.get_settings") as gs:
        s = MagicMock()
        s.wechat_api_v3_key = ""
        s.wechat_mch_id = ""
        s.is_development = False
        s.app_debug = False
        s.allow_payment_mock = False
        gs.return_value = s
        with pytest.raises(ValueError, match="未配置"):
            WechatPayVerifier().verify_and_parse({}, b'{"_dev_simulate": true}')


def test_alipay_verifier_requires_sign(monkeypatch):
    from app.services.payment_verify import AlipayVerifier

    with patch("app.services.payment_verify.get_settings") as gs:
        s = MagicMock()
        s.alipay_public_key = "x"
        s.alipay_app_id = "app"
        s.is_development = False
        s.allow_payment_mock = False
        gs.return_value = s
        with pytest.raises(ValueError, match="sign"):
            AlipayVerifier().verify_and_parse({}, b"out_trade_no=1&trade_status=TRADE_SUCCESS")


def test_inbox_event_id_unique_fields():
    a = make_event_id("MEITUAN", {"notify_id": "N9"}, b"x")
    assert a == "MEITUAN:N9"


def test_stock_unlimited_maps_to_platform():
    class P:
        product_no = "P1"
        name = "面"
        description = ""
        category_id = 1
        price = Decimal("10")
        box_price = Decimal("0")
        min_order_count = 1
        stock = 999
        main_image = ""

    payload = ProductSyncService._build_product_payload_cached(P(), None, "shop1")
    assert payload["stock"] == 9999


def test_multi_frame_tracker_confirms():
    import numpy as np
    from app.services.face_liveness import MultiFrameTracker

    tr = MultiFrameTracker(confirm_frames=3, ttl_seconds=5)
    # 每帧使用轻微变化但仍高度相似的 embedding；完全相同的帧会被
    # duplicate_cos 防重机制正确忽略，不能当作三份独立活体证据。
    embeddings = [
        np.asarray([np.cos(theta), np.sin(theta), 0, 0, 0, 0, 0, 0], dtype=np.float32)
        for theta in (0.0, 0.12, 0.24)
    ]
    ok1, _, _ = tr.push(1, embeddings[0], 7, 0.9)
    ok2, _, _ = tr.push(1, embeddings[1], 7, 0.91)
    ok3, cid, avg = tr.push(1, embeddings[2], 7, 0.92)
    assert (ok1, ok2, ok3) == (False, False, True)
    assert cid == 7 and avg >= 0.9


def test_liveness_rejects_bad_bytes():
    from app.services.face_liveness import check_liveness_simple

    with pytest.raises(ValueError):
        check_liveness_simple(b"not-an-image")
