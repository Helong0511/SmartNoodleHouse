"""手机号防冒用回归：注册/激活不收手机号；生产禁止手输，开发允许手输（未验证）。

安全目标：
- 生产环境手机号只能来自微信 getPhoneNumber 或支付宝官方授权密文，防止乱填他人号码。
- 开发/调试环境允许手输手机号，但仅保存为未验证（phone_verified=0），不合并账号、不升级会员。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.requests import Request

from app.api import miniprogram_member as mp
from app.core.exceptions import AppException


def _request(path: str = "/api/miniprogram/member/phone") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
        }
    )
    request.state.request_id = "test-request"
    return request


def test_register_and_activate_schemas_have_no_phone_field():
    """注册/激活模型不再声明 phone：客户端即使传了也会被 pydantic 忽略。"""
    assert "phone" not in mp.MemberRegisterRequest.model_fields
    assert "phone" not in mp.MemberActivateRequest.model_fields

    reg = mp.MemberRegisterRequest(nickname="张三", phone="13800000000")
    act = mp.MemberActivateRequest(nickname="张三", phone="13800000000")
    assert not hasattr(reg, "phone")
    assert not hasattr(act, "phone")


@pytest.mark.asyncio
async def test_member_phone_rejects_manual_input_in_production():
    """生产环境手输手机号必须拒绝（40054），不访问 DB。"""
    fake_settings = SimpleNamespace(is_development=False)
    with patch.object(mp, "get_settings", return_value=fake_settings):
        with pytest.raises(AppException) as exc:
            await mp.bind_member_phone(
                mp.PhoneBindRequest(phone="13800000000"),
                _request(),
                None,  # db：拒绝早于任何 DB 访问
                None,  # customer
            )
    assert exc.value.code == 40054


@pytest.mark.asyncio
async def test_member_phone_manual_input_allowed_in_dev_and_unverified():
    """开发环境手输手机号走 save_unverified_phone，保存为未验证、不合并。"""
    fake_settings = SimpleNamespace(is_development=True)
    customer = SimpleNamespace(id=1, phone="", phone_verified=0)

    async def fake_save(db, cust, phone):
        cust.phone = phone
        cust.phone_verified = 0
        return cust

    with patch.object(mp, "get_settings", return_value=fake_settings), \
         patch.object(mp.CustomerService, "save_unverified_phone", side_effect=fake_save), \
         patch.object(mp.CustomerService, "refresh_profile_status", new=Mock()), \
         patch.object(mp.CustomerService, "build_member_profile", new=AsyncMock(return_value={})), \
         patch.object(mp.CouponService, "welcome_template_name", new=AsyncMock(return_value="新客券")), \
         patch.object(mp.AuditService, "log", new=AsyncMock()), \
         patch.object(mp, "_issue_customer_token", return_value="tok"):
        db = SimpleNamespace(flush=AsyncMock())
        result = await mp.bind_member_phone(
            mp.PhoneBindRequest(phone="13800000000"),
            _request(),
            db,
            customer,
        )

    data = result["data"]
    assert data["phone"] == "13800000000"
    assert data["phone_verified"] is False
    assert data["account_merged"] is False


@pytest.mark.asyncio
async def test_member_phone_requires_exactly_one_official_credential():
    """无官方授权凭据时拒绝（40054）。"""
    with pytest.raises(AppException) as exc:
        await mp.bind_member_phone(mp.PhoneBindRequest(), _request(), None, None)
    assert exc.value.code == 40054


@pytest.mark.asyncio
async def test_mock_debug_credential_binds_fixed_phone_in_dev():
    """开发环境 mock:debug 凭据走完整绑定链路，返回固定测试号且未验证。"""
    fake_settings = SimpleNamespace(is_development=True)
    customer = SimpleNamespace(id=1, phone="", phone_verified=0)

    async def fake_save(db, cust, phone):
        cust.phone = phone
        cust.phone_verified = 0
        return cust

    with patch.object(mp, "get_settings", return_value=fake_settings), \
         patch.object(mp.CustomerService, "save_unverified_phone", side_effect=fake_save), \
         patch.object(mp.CustomerService, "refresh_profile_status", new=Mock()), \
         patch.object(mp.CustomerService, "build_member_profile", new=AsyncMock(return_value={})), \
         patch.object(mp.CouponService, "welcome_template_name", new=AsyncMock(return_value="新客券")), \
         patch.object(mp.AuditService, "log", new=AsyncMock()), \
         patch.object(mp, "_issue_customer_token", return_value="tok"):
        db = SimpleNamespace(flush=AsyncMock())
        result = await mp.bind_member_phone(
            mp.PhoneBindRequest(phone_code="mock:debug"),
            _request(),
            db,
            customer,
        )

    data = result["data"]
    assert data["phone"] == mp._MOCK_DEBUG_PHONE
    assert data["phone_verified"] is False
    assert data["account_merged"] is False


@pytest.mark.asyncio
async def test_mock_debug_credential_rejected_in_production():
    """生产环境 mock:debug 凭据拒绝（40054），不访问 DB。"""
    fake_settings = SimpleNamespace(is_development=False)
    with patch.object(mp, "get_settings", return_value=fake_settings):
        with pytest.raises(AppException) as exc:
            await mp.bind_member_phone(
                mp.PhoneBindRequest(phone_code="mock:debug"),
                _request(),
                None,
                None,
            )
    assert exc.value.code == 40054
