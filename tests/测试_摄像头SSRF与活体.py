"""P1 large: 摄像头 SSRF 防护 + 人脸活体挑战。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.services.camera_credentials import (
    assert_camera_url_safe,
    prepare_camera_source,
)


# ─── Camera SSRF ───


def test_camera_url_blocks_metadata_endpoint():
    """禁止 169.254.169.254 云元数据端点。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@169.254.169.254/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_camera_url_blocks_localhost():
    """禁止 127.0.0.1 回环地址。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@127.0.0.1/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_camera_url_blocks_link_local():
    """禁止 169.254.x.x 链路本地地址。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@169.254.1.1/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_camera_url_allows_private_lan():
    """允许 10.x/192.168.x 内网摄像头（设计预期）。"""
    assert_camera_url_safe("rtsp://admin:pass@192.168.1.100/Streaming/Channels/101")
    assert_camera_url_safe("rtsp://admin:pass@10.0.0.5/Streaming/Channels/101")
    assert_camera_url_safe("rtsp://admin:pass@172.16.0.1/Streaming/Channels/101")


def test_camera_url_allows_public_ip():
    """允许公网 IP。"""
    assert_camera_url_safe("rtsp://admin:pass@8.8.8.8/Streaming/Channels/101")


def test_camera_url_blocks_dns_to_loopback(monkeypatch):
    """hostname 解析到回环时拒绝（缓解 DNS rebinding）。"""

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr("app.services.camera_credentials.socket.getaddrinfo", _fake_getaddrinfo)
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@evil.example/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_camera_url_blocks_localhost_hostname():
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@localhost/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_camera_url_ignores_non_url():
    """USB 数字源（纯数字）跳过校验。"""
    assert_camera_url_safe("1")
    assert_camera_url_safe("")


def test_prepare_camera_source_blocks_metadata():
    """prepare_camera_source 应对元数据端点 URL 抛错。"""
    with pytest.raises(AppException) as ei:
        prepare_camera_source("rtsp://admin:pass@169.254.169.254/Streaming/Channels/101")
    assert ei.value.code == 40026


def test_prepare_camera_source_allows_safe_lan():
    """prepare_camera_source 应允许正常内网 RTSP URL。"""
    safe, user, pwd = prepare_camera_source("rtsp://admin:secret@192.168.1.100/Streaming/Channels/101")
    assert "192.168.1.100" in safe
    assert user == "admin"
    assert pwd == "secret"
    # 密码不应残留在 URL 中
    assert "secret" not in safe


# ─── Face Liveness Challenge ───


@pytest.mark.asyncio
async def test_generate_liveness_challenge_returns_token_and_action():
    """挑战生成应返回 token、action、mode=challenge。"""
    from app.services.face_liveness_service import LIVENESS_ACTIONS, generate_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        result = await generate_liveness_challenge(customer_id=1, camera_id=2)
    assert result["mode"] == "challenge"
    assert result["token"]
    assert result["action"] in LIVENESS_ACTIONS
    assert result["expires_at"]


@pytest.mark.asyncio
async def test_verify_liveness_challenge_success():
    """token 有效 + 动作完成 → True。"""
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    payload = '{"action": "blink", "customer_id": 1, "camera_id": 2}'
    # 原子 GETDEL 由单条 Lua eval 完成
    fake_redis.eval = AsyncMock(return_value=payload)
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        ok = await verify_liveness_challenge("valid_token", action_completed=True)
    assert ok is True
    fake_redis.eval.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_liveness_challenge_replay_blocked():
    """同一 token 第二次校验应失败（Lua GETDEL 已在第一次删除）。"""
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value=None)  # 已被第一次 GETDEL 删除
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        ok = await verify_liveness_challenge("replayed_token", action_completed=True)
    assert ok is False


@pytest.mark.asyncio
async def test_verify_liveness_challenge_action_not_completed():
    """未显式 action_completed → False，且不消费 token（允许重试）。"""
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value='{"action": "blink"}')
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        ok = await verify_liveness_challenge("valid_token", action_completed=False)
    assert ok is False
    fake_redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_liveness_challenge_claimed_action_mismatch():
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value='{"action": "blink", "customer_id": 1}')
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        ok = await verify_liveness_challenge(
            "valid_token", action_completed=True, claimed_action="nod"
        )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_liveness_challenge_expired_token():
    """token 过期/不存在 → False。"""
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value=None)
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(
            face_liveness_mode="challenge", face_liveness_challenge_timeout=30
        ),
    ):
        ok = await verify_liveness_challenge("expired_or_invalid_token")
    assert ok is False
