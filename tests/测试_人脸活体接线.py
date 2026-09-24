"""人脸活体接线：challenge API、consent 强制校验、顾客绑定。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException


@pytest.mark.asyncio
async def test_generate_challenge_includes_action_label():
    from app.services.face_liveness_service import LIVENESS_ACTIONS, generate_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    with patch(
        "app.services.face_liveness_service.try_get_redis",
        AsyncMock(return_value=fake_redis),
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=SimpleNamespace(face_liveness_mode="challenge", face_liveness_challenge_timeout=30),
    ):
        result = await generate_liveness_challenge(customer_id=42)
    assert result["mode"] == "challenge"
    assert result["token"]
    assert result["action"] in LIVENESS_ACTIONS
    assert result["action_label"]
    assert result["expires_at"]
    fake_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_rejects_customer_mismatch():
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value='{"action":"blink","customer_id":1}')
    with patch(
        "app.services.face_liveness_service.try_get_redis",
        AsyncMock(return_value=fake_redis),
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=SimpleNamespace(face_liveness_mode="challenge"),
    ):
        ok = await verify_liveness_challenge("tok", action_completed=True, customer_id=99)
    assert ok is False


@pytest.mark.asyncio
async def test_enforce_liveness_required_in_challenge_mode():
    from app.api.v1 import miniprogram as mp

    with patch(
        "app.api.v1.miniprogram.get_settings",
        return_value=SimpleNamespace(face_liveness_mode="challenge"),
    ), patch(
        "app.services.face_liveness_service.verify_liveness_challenge",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with pytest.raises(AppException) as ei:
            await mp._enforce_liveness_challenge(1, liveness_token="", action_completed=True)
    assert ei.value.code == 40037


@pytest.mark.asyncio
async def test_enforce_liveness_skipped_when_mode_none():
    from app.api.v1 import miniprogram as mp

    with patch(
        "app.api.v1.miniprogram.get_settings",
        return_value=SimpleNamespace(face_liveness_mode="none"),
    ):
        mode = await mp._enforce_liveness_challenge(1, liveness_token=None, action_completed=False)
    assert mode == "none"


@pytest.mark.asyncio
async def test_consent_base64_requires_liveness_before_decode():
    """缺 token 时在解码图片前即拒绝，避免无挑战也能注册。"""
    from app.api.v1.miniprogram import consent_face_base64

    customer = SimpleNamespace(id=7)
    req = MagicMock()
    req.json = AsyncMock(
        return_value={
            "image_base64": "a" * 900,
            # 故意缺 liveness_token
        }
    )
    with patch(
        "app.api.v1.miniprogram._enforce_liveness_challenge",
        new_callable=AsyncMock,
        side_effect=AppException(40037, "活体校验未通过"),
    ):
        with pytest.raises(AppException) as ei:
            await consent_face_base64(req, AsyncMock(), customer)
    assert ei.value.code == 40037


@pytest.mark.asyncio
async def test_alipay_new_customer_has_customer_no():
    from app.services.customer_service import CustomerService

    cust = CustomerService._new_guest_customer()
    assert cust.customer_no
    assert str(cust.customer_no).strip()
