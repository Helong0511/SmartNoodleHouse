"""业务参数进库：bool / enum / string 校验与读库。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.system_settings_service import BUSINESS_SETTING_META, SystemSettingsService


def test_hotreload_keys_in_business_meta():
    for key in (
        "delivery_auto_confirm",
        "welcome_coupon_name",
        "enable_wx_phone_bind",
        "kitchen_ticket_order_no",
        "xpyun_print_copies",
        "max_upload_size_mb",
        "jwt_expire_minutes",
        "customer_jwt_expire_minutes",
        "webhook_ip_allowlist",
        "webhook_require_allowlist",
        "trusted_proxy_ips",
        "h5_public_base_url",
        "mini_program_env_version",
        "xpyun_enabled",
        "printer_mode",
        "printer_host",
        "printer_port",
        "wechat_subscribe_template_order_ready",
        "allowed_image_extensions",
        "app_timezone",
        "delivery_env_fallback_store_id",
    ):
        assert key in BUSINESS_SETTING_META
        assert BUSINESS_SETTING_META[key].get("group")


def test_store_scope_split():
    assert SystemSettingsService.allows_store_override("show_menu_search") is True
    assert SystemSettingsService.allows_store_override("show_menu_smart_taste") is True
    assert SystemSettingsService.allows_store_override("welcome_coupon_name") is True
    assert SystemSettingsService.allows_store_override("printer_host") is True
    assert SystemSettingsService.allows_store_override("order_payment_timeout_minutes") is True
    assert SystemSettingsService.allows_store_override("display_screen_token") is True
    assert SystemSettingsService.allows_store_override("invoice_enabled") is True
    assert SystemSettingsService.allows_store_override("jwt_expire_minutes") is False
    assert SystemSettingsService.allows_store_override("enable_wx_phone_bind") is False
    assert SystemSettingsService.allows_store_override("h5_public_base_url") is False


def test_business_groups_cover_meta():
    from app.services.system_settings_service import BUSINESS_SETTING_GROUPS

    ids = {g["id"] for g in BUSINESS_SETTING_GROUPS}
    for meta in BUSINESS_SETTING_META.values():
        assert meta["group"] in ids



def test_validate_bool_and_enum():
    assert SystemSettingsService._validate_value("delivery_auto_confirm", "true") == "true"
    assert SystemSettingsService._validate_value("delivery_auto_confirm", "0") == "false"
    assert SystemSettingsService._validate_value("kitchen_ticket_order_no", "off") == "off"
    with pytest.raises(ValueError):
        SystemSettingsService._validate_value("kitchen_ticket_order_no", "qrcode")
    assert SystemSettingsService._validate_value("welcome_coupon_name", "") == ""
    assert SystemSettingsService._validate_value("welcome_coupon_name", "新客满20减3") == "新客满20减3"
    assert SystemSettingsService._validate_value("mini_program_env_version", "") == "auto"
    assert SystemSettingsService._validate_value("printer_mode", "local") == "local"
    assert SystemSettingsService._validate_value("app_timezone", "Asia/Shanghai") == "Asia/Shanghai"
    with pytest.raises(ValueError):
        SystemSettingsService._validate_value("app_timezone", "Not/AZone")
    assert SystemSettingsService._validate_value("h5_public_base_url", "https://order.example.com") == (
        "https://order.example.com"
    )
    with pytest.raises(ValueError):
        SystemSettingsService._validate_value("h5_public_base_url", "order.example.com")


@pytest.mark.asyncio
async def test_effective_bool():
    db = MagicMock()
    with patch.object(
        SystemSettingsService,
        "effective_setting",
        AsyncMock(return_value="true"),
    ):
        assert await SystemSettingsService.effective_bool(db, "delivery_auto_confirm", False) is True
    with patch.object(
        SystemSettingsService,
        "effective_setting",
        AsyncMock(return_value="false"),
    ):
        assert await SystemSettingsService.effective_bool(db, "delivery_auto_confirm", True) is False


def test_parse_bool_env_style():
    assert SystemSettingsService._parse_bool("True", False) is True
    assert SystemSettingsService._parse_bool("False", True) is False
    assert SystemSettingsService._parse_bool(True) is True


def test_resolved_mini_program_env_version_sync():
    SystemSettingsService.invalidate_cache_local()
    SystemSettingsService._cache = {"mini_program_env_version": "trial"}
    SystemSettingsService._cache_at = 1e18
    SystemSettingsService._cache_ver = 0
    assert SystemSettingsService.resolved_mini_program_env_version_sync() == "trial"
    SystemSettingsService.invalidate_cache_local()
