"""小程序公开门店入口；顾客中心/登录/菜单等已拆至 siblings。"""
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Request

from app.api.deps import DbSession
from app.core.exceptions import AppException
from app.core.response import success

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

# 人脸 / 菜单 / 堂食 / 登录 / 顾客中心：兼容测试 patch 与旧导入
from app.api.miniprogram_face import (  # noqa: E402
    FaceConsentBase64Request,
    _enforce_liveness_challenge,
    _face_status,
    _require_face_welcome_enabled,
    consent_face_base64,
)
from app.api.miniprogram_menu import (  # noqa: E402
    _load_owned_order,
    get_menu_bundle_api,
    get_recommendations,
    submit_order_feedback,
)
from app.api.miniprogram_dine import (  # noqa: E402
    resolve_table as _resolve_table,
    resolve_table_record as _resolve_table_record,
    store_brief as _store_brief,
    table_payload as _table_payload,
)
from app.api.miniprogram_auth import (  # noqa: E402
    AlipayMiniLoginRequest,
    WechatMiniLoginRequest,
    _channel_login_payload,
    _consume_login_credential,
    _consume_refresh_rotation,
    _customer_channel_from_request,
    _finalize_channel_login,
    _issue_customer_token,
    _persistable_avatar_url,
    alipay_mini_login,
    customer_logout,
    refresh_customer_token,
    wechat_mini_login,
)
from app.api.miniprogram_customer import (  # noqa: E402
    PrivacySettingsPatch,
    PointsExchangeBody,
    clear_my_taste_profile,
    exchange_my_points,
    get_my_exchange_options,
    get_my_face_status,
    get_my_points_ledger,
    get_my_preferences,
    get_my_privacy_settings,
    get_my_profile,
    get_my_taste_profile,
    list_my_coupons,
    list_my_vouchers,
    update_my_privacy_settings,
)
from app.core.cache_keys import LOGIN_CREDENTIAL_TTL_SECONDS  # noqa: F401,E402
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth  # noqa: F401,E402


@router.get("/stores/{store_id}")
async def get_public_store(store_id: int, request: Request, db: DbSession):
    """小程序公开门店信息（无需登录）。"""
    store = await _store_brief(db, store_id)
    if not store:
        raise AppException(40405, "门店不存在或已停业")
    return success(store, request=request)
