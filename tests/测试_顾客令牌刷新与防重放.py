"""P2-6 顾客令牌刷新 / P2-7 登录凭证防重放 / P2-16 登录共用收尾 / P2-11 顾客侧错误码统一 回归。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError, jwt

from app.api import miniprogram as mp
from app.core.exceptions import AppException
from app.core.security import create_access_token, decode_token


JWT_SECRET = "s" * 48


def _full_settings(**over):
    base = dict(
        jwt_secret=JWT_SECRET,
        jwt_algorithm="HS256",
        jwt_expire_minutes=60,
        customer_jwt_expire_minutes=120,
        app_name="SmartNoodleHouse",
        jwt_audience="smart-noodle-house",
        customer_refresh_grace_hours=24,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _request(authorization: str | None):
    headers = {"authorization": authorization} if authorization else {}
    return SimpleNamespace(headers=headers, state=SimpleNamespace(request_id="t"))


def _customer(cid=7, ver=0):
    return SimpleNamespace(
        id=cid,
        customer_no=f"CU-{cid}",
        status=1,
        account_status="ACTIVE",
        token_version=ver,
        member_status="MEMBER",
        first_seen_store_id=1,
    )


def _db(customer):
    db = MagicMock()
    db.get = AsyncMock(return_value=customer)
    return db


def _refresh_patches(settings, redis=None):
    """统一 patch：security/config 的 get_settings + system_settings TTL + redis。"""
    return (
        patch("app.core.security.get_settings", return_value=settings),
        patch("app.api.miniprogram_auth.get_settings", return_value=settings),
        patch(
            "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
            side_effect=lambda key, fallback: settings.customer_jwt_expire_minutes,
        ),
        patch("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=redis)),
    )


def _fake_redis(*, eval_result=1, set_result=True):
    r = MagicMock()
    r.eval = AsyncMock(return_value=eval_result)
    r.set = AsyncMock(return_value=set_result)
    return r


def _expired_customer_token(cid=7, ver=0, minutes_ago=30):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(cid),
        "exp": now - timedelta(minutes=minutes_ago),
        "iat": now - timedelta(minutes=90),
        "nbf": now - timedelta(minutes=90),
        "jti": "test-jti",
        "iss": "SmartNoodleHouse",
        "aud": "smart-noodle-house",
        "typ": "customer",
        "channel": "wechat",
        "ver": ver,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def _call_refresh(token, settings, redis=None, customer=None):
    patches = _refresh_patches(settings, redis)
    with patches[0], patches[1], patches[2], patches[3]:
        return await mp.refresh_customer_token(
            _request(f"Bearer {token}"),
            _db(customer if customer is not None else _customer()),
        )


# ─── P2-6：令牌刷新 ───


@pytest.mark.asyncio
async def test_refresh_with_valid_token_returns_new_customer_token():
    token = _expired_customer_token(minutes_ago=-60)  # 未过期
    settings = _full_settings()
    patches = _refresh_patches(settings)
    with patches[0], patches[1], patches[2], patches[3]:
        resp = await mp.refresh_customer_token(
            _request(f"Bearer {token}"), _db(_customer())
        )
        assert resp["code"] == 0
        data = resp["data"]
        assert data["customer_id"] == 7
        assert data["channel"] == "wechat"
        new_payload = decode_token(data["customer_token"])  # 同一 patched 作用域内解码
    assert new_payload["typ"] == "customer"
    assert new_payload["exp"] > datetime.now(timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_refresh_expired_token_within_grace_window():
    token = _expired_customer_token(minutes_ago=30)  # 过期 30 分钟 < 24h 宽限
    resp = await _call_refresh(token, _full_settings(customer_refresh_grace_hours=24))
    assert resp["code"] == 0
    assert resp["data"]["customer_token"]


@pytest.mark.asyncio
async def test_refresh_expired_token_beyond_grace_rejected():
    token = _expired_customer_token(minutes_ago=30)
    with pytest.raises(AppException) as ei:
        await _call_refresh(token, _full_settings(customer_refresh_grace_hours=0))
    assert ei.value.code == 40103
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_when_token_version_mismatch():
    token = _expired_customer_token(minutes_ago=-60, ver=0)
    with pytest.raises(AppException) as ei:
        await _call_refresh(token, _full_settings(), customer=_customer(ver=1))
    assert ei.value.code == 40101


@pytest.mark.asyncio
async def test_refresh_rejects_disabled_customer():
    token = _expired_customer_token(minutes_ago=-60)
    banned = SimpleNamespace(
        id=7, customer_no="CU-7", status=0, account_status="ACTIVE", token_version=0,
    )
    with pytest.raises(AppException) as ei:
        await _call_refresh(token, _full_settings(), customer=banned)
    assert ei.value.code == 40102


@pytest.mark.asyncio
async def test_refresh_rotation_blocks_second_use_of_same_token():
    token = _expired_customer_token(minutes_ago=-60)
    redis = _fake_redis(eval_result=0)  # Lua 判定：该 exp 已消费过
    with pytest.raises(AppException) as ei:
        await _call_refresh(token, _full_settings(), redis=redis)
    assert ei.value.code == 40105


@pytest.mark.asyncio
async def test_refresh_fails_open_when_redis_unavailable():
    token = _expired_customer_token(minutes_ago=-60)
    resp = await _call_refresh(token, _full_settings(), redis=None)
    assert resp["code"] == 0


@pytest.mark.asyncio
async def test_refresh_requires_bearer_token():
    with pytest.raises(AppException) as ei:
        patches = _refresh_patches(_full_settings())
        with patches[0], patches[1], patches[2], patches[3]:
            await mp.refresh_customer_token(_request(None), _db(_customer()))
    assert ei.value.code == 40100


@pytest.mark.asyncio
async def test_refresh_rejects_staff_token():
    with patch("app.core.security.get_settings", return_value=_full_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        staff = create_access_token(1)
    with pytest.raises(AppException) as ei:
        await _call_refresh(staff, _full_settings())
    assert ei.value.code == 40101


# ─── P2-7：登录凭证一次性消费 ───


@pytest.mark.asyncio
async def test_login_credential_first_use_allowed():
    redis = _fake_redis(set_result=True)
    with patch("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=redis)):
        await mp._consume_login_credential("wechat", "real-code-123")
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert kwargs.get("nx") is True
    assert kwargs.get("ex") == mp.LOGIN_CREDENTIAL_TTL_SECONDS


@pytest.mark.asyncio
async def test_login_credential_reuse_rejected():
    redis = _fake_redis(set_result=False)  # SET NX 失败 = 凭证已存在
    with patch("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=redis)):
        with pytest.raises(AppException) as ei:
            await mp._consume_login_credential("alipay", "real-auth-code")
    assert ei.value.code == 40104
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_login_credential_mock_prefix_skipped():
    redis = _fake_redis(set_result=False)
    with patch("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=redis)):
        # mock: 凭证跳过一次性消费，本地可反复调试
        await mp._consume_login_credential("wechat", "mock:simulator")
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_credential_fails_open_when_redis_down():
    with patch("app.api.miniprogram_auth.try_get_redis", AsyncMock(return_value=None)):
        await mp._consume_login_credential("wechat", "real-code")  # 不抛异常


# ─── P2-16：登录共用收尾（微信冒烟 + 凭证消费挂载） ───


@pytest.mark.asyncio
async def test_wechat_login_uses_shared_finalize_and_credential_guard():
    consumed = []

    async def fake_consume(channel, cred):
        consumed.append((channel, cred))

    async def fake_finalize(db, **kwargs):
        assert kwargs["channel"] == "wechat"
        assert kwargs["account"] is acc
        return {"customer_id": 7}

    class Result:
        def scalar_one_or_none(self):
            return acc

    acc = SimpleNamespace(profile_completed=1)
    db = SimpleNamespace(execute=AsyncMock(return_value=Result()))
    settings = _full_settings()
    with patch("app.api.miniprogram_auth._consume_login_credential", fake_consume), patch(
        "app.api.miniprogram_auth._finalize_channel_login", fake_finalize
    ), patch("app.api.miniprogram_auth.WechatMiniAuth") as auth_cls, patch(
        "app.api.miniprogram_auth.CustomerService.get_or_create_by_wechat", AsyncMock(return_value=_customer())
    ):
        auth_cls.return_value.code2session = AsyncMock(
            return_value={"openid": "wx-test", "unionid": None, "is_mock": False}
        )
        resp = await mp.wechat_mini_login(
            mp.WechatMiniLoginRequest(code="real-code", table_token=None), _request(None), db
        )
    assert resp["code"] == 0
    assert consumed == [("wechat", "real-code")]


# ─── P2-11：顾客侧 AppException 错误码 ───


@pytest.mark.asyncio
async def test_get_current_customer_unauthenticated_uses_app_exception():
    from app.api.deps import get_current_customer

    with pytest.raises(AppException) as ei:
        await get_current_customer(_request(None), MagicMock(), authorization=None)
    assert ei.value.code == 40100
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_customer_disabled_raises_40102():
    from app.api.deps import get_current_customer

    with patch("app.core.security.get_settings", return_value=_full_settings()), patch(
        "app.services.system_settings_service.SystemSettingsService.resolved_int_sync",
        return_value=60,
    ):
        token = _expired_customer_token(minutes_ago=-60)
        db = _db(SimpleNamespace(id=7, status=1, account_status="BANNED", token_version=0))
        with pytest.raises(AppException) as ei:
            await get_current_customer(_request(f"Bearer {token}"), db, authorization=f"Bearer {token}")
    assert ei.value.code == 40102
    assert ei.value.status_code == 401
