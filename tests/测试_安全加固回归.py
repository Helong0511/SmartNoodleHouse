"""安全与财务小回归：登录锁维度、日志脱敏、积分取整、Leader fence。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.v1.auth import _login_fail_key, _login_lock_key, _login_scope
from app.api.v1.client_logs import _redact_secrets
from app.core.leader_lock import is_leader_held, set_leader_held
from app.core.response import money
from app.services.customer_service import CustomerService


def test_login_lock_scoped_by_username_and_ip():
    assert _login_scope("Admin", "1.2.3.4") == "admin|1.2.3.4"
    assert _login_fail_key("a", "10.0.0.1") != _login_fail_key("a", "10.0.0.2")
    assert _login_lock_key("u", "x") != _login_lock_key("u", "y")


def test_client_log_redacts_bearer_and_tokens():
    s = _redact_secrets('Authorization: Bearer abcdefghijklmnop token=secretvalue99')
    assert "abcdefghijklmnop" not in s
    assert "***" in s


def test_points_from_amount_rounds_half_up():
    assert CustomerService.points_from_amount(Decimal("9.50")) == 10
    assert CustomerService.points_from_amount(Decimal("9.49")) == 9
    assert CustomerService.points_from_amount("18.00") == 18
    assert CustomerService.points_from_amount(money("0.4")) == 0


def test_leader_held_flag():
    set_leader_held(True)
    assert is_leader_held() is True
    set_leader_held(False)
    assert is_leader_held() is False


def test_face_key_version_includes_key_digest(monkeypatch):
    from app.core.config import get_settings
    from app.services.face_service import FaceEncryption

    monkeypatch.setenv("FACE_ENCRYPTION_KEY", "ci-test-face-encryption-key-32b!!")
    get_settings.cache_clear()
    v1 = FaceEncryption.key_version_label()
    assert v1.startswith("enc1:")
    assert len(v1) <= 20
    monkeypatch.setenv("FACE_ENCRYPTION_KEY", "another-face-encryption-key-32byt!!")
    get_settings.cache_clear()
    v2 = FaceEncryption.key_version_label()
    assert v1 != v2
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_face_reencrypt_stale_profiles(db_session, monkeypatch):
    import json

    from app.core.response import now_local
    from app.models import Customer, FaceProfile
    from app.services.face_service import FaceEncryption, FaceService

    monkeypatch.setenv("FACE_ENCRYPTION_KEY", "ci-test-face-encryption-key-32b!!")
    from app.core.config import get_settings

    get_settings.cache_clear()

    cust = Customer(id=8801, customer_no="C-FACE-RE", nickname="重加密", status=1)
    db_session.add(cust)
    await db_session.flush()
    enc = FaceEncryption()
    vec = [0.01] * 512
    # 故意写成旧 version，触发迁移
    profile = FaceProfile(
        id=8802,
        customer_id=cust.id,
        feature_data=enc.encrypt(json.dumps(vec).encode()),
        feature_version="v1",
        consent_status="AUTHORIZED",
        consent_at=now_local(),
    )
    db_session.add(profile)
    await db_session.commit()

    n = await FaceService.reencrypt_stale_profiles(db_session, limit=10)
    await db_session.commit()
    assert n == 1
    await db_session.refresh(profile)
    assert profile.feature_version == FaceEncryption.key_version_label()
    assert json.loads(enc.decrypt(profile.feature_data).decode()) == vec
    get_settings.cache_clear()
