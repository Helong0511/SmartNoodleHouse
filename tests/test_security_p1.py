"""P1 安全加固冒烟：加密服务 / 净营收负值 / 状态机取消 / 摄像头 URL 剥离。"""
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from app.services.camera_credentials import (
    ensure_no_plaintext_password_in_url,
    extract_url_credentials,
    prepare_camera_source,
)
from app.services.order_state_machine import assert_staff_transition, FORBIDDEN_STAFF_TARGETS
from app.services.secret_encryption import SecretEncryptionService


def test_secret_encryption_roundtrip_and_version(monkeypatch):
    monkeypatch.setenv("FACE_ENCRYPTION_KEY", "unit-test-face-key-32bytes!!!!!")
    from app.core.config import get_settings

    get_settings.cache_clear()
    plain = "eleme-access-token-xyz"
    enc = SecretEncryptionService.encrypt(plain)
    assert enc and enc.startswith("snhenc:v1:")
    assert SecretEncryptionService.key_version(enc) == "v1"
    assert SecretEncryptionService.decrypt(enc) == plain
    # 明文兼容
    assert SecretEncryptionService.decrypt("legacy-plain") == "legacy-plain"
    get_settings.cache_clear()


def test_camera_source_strips_password():
    safe, user, pwd = prepare_camera_source(
        "rtsp://admin:secret@192.168.1.10:554/Streaming/Channels/102"
    )
    assert pwd == "secret"
    assert user == "admin"
    assert "secret" not in safe
    parsed = urlparse(safe)
    assert parsed.password is None
    with pytest.raises(Exception):
        ensure_no_plaintext_password_in_url(
            "rtsp://admin:secret@192.168.1.10/Streaming/Channels/102"
        )
    # extract 后的 safe 不应再抛
    ensure_no_plaintext_password_in_url(safe)


def test_pending_payment_to_cancelled_still_allowed():
    assert "CANCELLED" in FORBIDDEN_STAFF_TARGETS
    assert_staff_transition("PENDING_PAYMENT", "CANCELLED")
    with pytest.raises(Exception):
        assert_staff_transition("PAID", "CANCELLED")


def test_net_revenue_allows_negative():
    """回归：负净收入不得被强制夹为 0。"""
    from app.core.response import money

    net = money(Decimal("10.00") - Decimal("25.00"))
    assert net == Decimal("-15.00")
    assert net < 0
