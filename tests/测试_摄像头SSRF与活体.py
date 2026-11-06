"""P1 large: 摄像头 SSRF 防护 + 人脸活体挑战。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.api.miniprogram_face import _is_supported_face_image
from app.api.cameras import _is_valid_visitor_snapshot_bytes
from app.services.visitor_snapshot_access import build_visitor_snapshot_url, verify_visitor_snapshot_signature
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


@pytest.mark.parametrize(
    "url",
    [
        "rtsp://admin:pass@[::ffff:127.0.0.1]/Streaming/Channels/101",
        "rtsp://admin:pass@[::ffff:169.254.169.254]/Streaming/Channels/101",
    ],
)
def test_camera_url_blocks_ipv4_mapped_ipv6_bypass(url):
    """IPv4-mapped IPv6 必须继承映射 IPv4 的回环/链路本地拦截规则。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe(url)
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


def test_camera_url_blocks_alibaba_metadata_endpoint():
    """禁止阿里云 ECS 元数据地址，避免窃取实例角色凭据。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://admin:pass@100.100.100.200/Streaming/Channels/101")
    assert ei.value.code == 40026


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://192.168.1.100/camera",
        "rtsp:///Streaming/Channels/101",
    ],
)
def test_camera_url_rejects_unsupported_or_malformed_url(url):
    """URL-like 输入不能因协议不支持或缺失主机而静默放行。"""
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe(url)
    assert ei.value.code == 40026


@pytest.mark.parametrize(
    "image_bytes",
    [
        b"\xff\xd8\xff" + b"\x00" * 16,
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
    ],
)
def test_miniprogram_face_accepts_real_jpeg_png_magic_bytes(image_bytes):
    assert _is_supported_face_image(image_bytes) is True


@pytest.mark.parametrize(
    "image_bytes",
    [
        b"BM" + b"\x00" * 32,
        b"GIF89a" + b"\x00" * 32,
        b"\xff\xd8" + b"\x00" * 32,
        b"\x89PNG" + b"\x00" * 32,
    ],
)
def test_miniprogram_face_rejects_unsupported_or_truncated_magic_bytes(image_bytes):
    assert _is_supported_face_image(image_bytes) is False


@pytest.mark.asyncio
async def test_verify_liveness_challenge_rejects_claimed_action_mismatch():
    from app.services.face_liveness_service import verify_liveness_challenge

    fake_redis = MagicMock()
    fake_redis.eval = AsyncMock(return_value='{"action": "blink", "customer_id": 1}')
    with patch(
        "app.services.face_liveness_service.try_get_redis", AsyncMock(return_value=fake_redis)
    ), patch(
        "app.services.face_liveness_service.get_settings",
        return_value=__import__("types").SimpleNamespace(face_liveness_mode="challenge"),
    ):
        ok = await verify_liveness_challenge(
            "valid_token",
            action_completed=True,
            claimed_action="turn_left",
            customer_id=1,
        )
    assert ok is False


@pytest.mark.parametrize(
    "data,expected",
    [
        (b"\xff\xd8\xff" + b"\x00" * 128, True),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 128, False),
        (b"not-a-jpeg" + b"\x00" * 128, False),
        (b"", False),
    ],
)
def test_private_visitor_snapshot_accepts_only_jpeg_magic_and_size(data, expected):
    assert _is_valid_visitor_snapshot_bytes(data) is expected

 
def test_visitor_snapshot_signature_binds_event_store_and_object_key():
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlparse

    settings = SimpleNamespace(
        secret_encryption_key="current-secret-key",
        secret_encryption_key_previous="",
        face_encryption_key="face-key",
        jwt_secret="jwt-key",
    )
    with patch("app.services.visitor_snapshot_access.get_settings", return_value=settings):
        url = build_visitor_snapshot_url(10, 20, "visitors/2026/11/04/cam1_test.jpg", now=1_800_000_000)
        query = parse_qs(urlparse(url).query)
        exp = int(query["exp"][0])
        sig = query["sig"][0]
        assert verify_visitor_snapshot_signature(
            10, 20, "visitors/2026/11/04/cam1_test.jpg", exp, sig, now=1_800_000_001
        )
        assert not verify_visitor_snapshot_signature(
            11, 20, "visitors/2026/11/04/cam1_test.jpg", exp, sig, now=1_800_000_001
        )
        assert not verify_visitor_snapshot_signature(
            10, 21, "visitors/2026/11/04/cam1_test.jpg", exp, sig, now=1_800_000_001
        )
        assert not verify_visitor_snapshot_signature(
            10, 20, "visitors/2026/11/04/cam2_test.jpg", exp, sig, now=1_800_000_001
        )
        assert not verify_visitor_snapshot_signature(
            10, 20, "visitors/2026/11/04/cam1_test.jpg", exp, sig, now=exp + 1
        )

def test_camera_url_fails_closed_when_hostname_dns_lookup_fails(monkeypatch):
    """无法解析主机名时不得按安全目标放行。"""
    import socket

    def _fail_dns(host, port, *args, **kwargs):
        raise socket.gaierror("temporary DNS failure")

    monkeypatch.setattr("app.services.camera_credentials.socket.getaddrinfo", _fail_dns)
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://camera.example.invalid/Streaming/Channels/102")
    assert ei.value.code == 40026


def test_camera_worker_revalidates_target_before_open_and_reconnect(monkeypatch):
    """运行时每次连接前都必须重新校验，旧数据库配置不能绕过保存时 SSRF 检查。"""
    import camera.camera_manager as camera_manager

    def _reject(_source):
        raise AppException(40026, "DNS 解析到危险地址")

    monkeypatch.setattr(
        "app.services.camera_credentials.pin_camera_url_for_connect", _reject
    )
    capture = MagicMock()
    monkeypatch.setattr(camera_manager.cv2, "VideoCapture", capture)

    worker = camera_manager.CameraWorker(
        999, "rtsp://camera.example/Streaming/Channels/102", 25
    )
    assert worker._connect() is False
    capture.assert_not_called()
    assert worker.status == "OFFLINE"

def test_camera_connection_pins_hostname_to_validated_ip(monkeypatch):
    """RTSP hostname 在交给 FFmpeg 前必须固定成已校验 IP，消除 DNS rebinding TOCTOU。"""
    from urllib.parse import urlparse
    from app.services.camera_credentials import pin_camera_url_for_connect

    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (None, None, None, None, ("192.168.1.101", port or 554))
        ],
    )
    pinned = pin_camera_url_for_connect(
        "rtsp://camera-user:camera-pass@camera.example:554/Streaming/Channels/102"
    )
    parsed = urlparse(pinned)
    assert parsed.hostname == "192.168.1.101"
    assert parsed.port == 554
    assert parsed.username == "camera-user"
    assert parsed.password == "camera-pass"
    assert "camera.example" not in pinned


def test_camera_connection_rejects_mixed_safe_and_dangerous_dns_answers(monkeypatch):
    """DNS 轮询/多 A 记录中只要混入危险地址，整个目标都必须拒绝。"""
    from app.services.camera_credentials import pin_camera_url_for_connect

    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (None, None, None, None, ("192.168.1.101", port or 554)),
            (None, None, None, None, ("127.0.0.1", port or 554)),
        ],
    )
    with pytest.raises(AppException) as ei:
        pin_camera_url_for_connect("rtsp://camera.example/Streaming/Channels/102")
    assert ei.value.code == 40026


@pytest.mark.parametrize("url", [
    "rtsps://camera.example/Streaming/Channels/102",
    "https://camera.example/ISAPI/System/deviceInfo",
])
def test_camera_tls_hostname_requires_ip_literal(url, monkeypatch):
    from app.services.camera_credentials import pin_camera_url_for_connect

    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (None, None, None, None, ("192.168.1.101", port or 443))
        ],
    )
    with pytest.raises(AppException) as ei:
        pin_camera_url_for_connect(url)
    assert ei.value.code == 40026


def test_camera_worker_opens_pinned_ip_not_dns_hostname(monkeypatch):
    """运行时采集线程必须把固定 IP 交给 FFmpeg，而不是再次交给 DNS。"""
    import camera.camera_manager as camera_manager

    class FakeCapture:
        def __init__(self):
            self.source = None

        def set(self, *_args):
            return True

        def open(self, source, _backend):
            self.source = source
            return True

        def isOpened(self):
            return True

        def release(self):
            return None

    capture = FakeCapture()
    monkeypatch.setattr(camera_manager.cv2, "VideoCapture", lambda: capture)
    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (None, None, None, None, ("192.168.1.102", port or 554))
        ],
    )
    worker = camera_manager.CameraWorker(
        1001, "rtsp://camera.example/Streaming/Channels/102", 25
    )
    assert worker._connect() is True
    assert "192.168.1.102" in capture.source
    assert "camera.example" not in capture.source


@pytest.mark.parametrize(
    "url",
    [
        "rtsp://192.168.1.10:99999/Streaming/Channels/102",
        "rtsp://192.168.1.10:not-a-port/Streaming/Channels/102",
    ],
)
def test_camera_url_rejects_invalid_ports(url):
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe(url)
    assert ei.value.code == 40026


def test_camera_url_rejects_empty_dns_answer(monkeypatch):
    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda *_args, **_kwargs: [],
    )
    with pytest.raises(AppException) as ei:
        assert_camera_url_safe("rtsp://camera.example.invalid/Streaming/Channels/102")
    assert ei.value.code == 40026


def test_hikvision_isapi_base_uses_pinned_ip_not_dns_hostname(monkeypatch):
    from app.services.hikvision_audio import resolve_isapi_base

    monkeypatch.setattr(
        "app.services.camera_credentials.socket.getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (None, None, None, None, ("192.168.1.103", port or 554))
        ],
    )
    assert resolve_isapi_base("rtsp://camera.example/Streaming/Channels/102") == "http://192.168.1.103"
