"""P1: 摄像头密码解密失败时 fail-closed，不回退到明文 URL。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.exceptions import AppException
from app.services.camera_credentials import build_connect_source


def test_decrypt_failure_raises_not_fallback_to_plaintext():
    """解密失败时应抛错，不返回可能含明文密码的 source_url。"""
    source = "rtsp://admin:secret@10.0.0.1/Streaming/Channels/102"

    with patch(
        "app.services.camera_credentials.decrypt_password",
        side_effect=RuntimeError("decrypt failed"),
    ):
        with pytest.raises(AppException) as ei:
            build_connect_source(source, "admin", "corrupted_encrypted_blob")
        assert ei.value.code == 40026
        assert "解密失败" in ei.value.message


def test_decrypt_success_builds_url():
    """解密成功时正常重建 RTSP URL。"""
    source = "rtsp://10.0.0.1/Streaming/Channels/102"

    with patch(
        "app.services.camera_credentials.decrypt_password",
        return_value="mypassword",
    ):
        url = build_connect_source(source, "admin", "valid_encrypted")
    assert "admin" in url
    assert "mypassword" in url
    assert "10.0.0.1" in url


def test_no_encrypted_password_uses_url_embedded():
    """无加密密码字段时，从 URL 内嵌口令提取（历史脏数据兼容）。"""
    source = "rtsp://admin:secret@10.0.0.1/Streaming/Channels/102"
    url = build_connect_source(source, "admin", None)
    assert "admin" in url
    assert "secret" in url


def test_no_encrypted_no_url_password_returns_as_is():
    """无加密密码且 URL 无内嵌口令时，原样返回。"""
    source = "rtsp://10.0.0.1/Streaming/Channels/102"
    url = build_connect_source(source, None, None)
    assert url == source


def test_usb_digital_source_returns_as_is():
    """USB 数字源（纯数字）原样返回。"""
    url = build_connect_source("1", None, None)
    assert url == "1"
