"""小程序公开门店与顾客中心（登录/扫码/菜单等已拆至 siblings）。"""
import logging

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from fastapi import APIRouter, Request

from app.api.deps import CurrentCustomer, DbSession
from app.core.exceptions import AppException
from app.core.response import serialize_dt, success
from app.models import CouponTemplate, CustomerPreference
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService, PreferenceService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

# 人脸 / 菜单 / 堂食 / 登录：兼容测试 patch 与旧导入
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
from app.core.cache_keys import LOGIN_CREDENTIAL_TTL_SECONDS  # noqa: F401,E402
from app.services.miniprogram_auth import WechatMiniAuth  # noqa: F401,E402


@router.get("/stores/{store_id}")
async def get_public_store(store_id: int, request: Request, db: DbSession):
    """小程序公开门店信息（无需登录）。"""
    store = await _store_brief(db, store_id)
    if not store:
        raise AppException(40405, "门店不存在或已停业")
    return success(store, request=request)


@router.get("/coupons/mine")
async def list_my_coupons(request: Request, db: DbSession, customer: CurrentCustomer, store_id: int | None = None):
    """我的可用优惠券（满减/折扣，不含代金券）。传 store_id 时仅返回该门店可用券。"""
    items = await CouponService.list_available(db, customer.id, store_id=store_id)
    return success({"items": items}, request=request)


@router.get("/vouchers/mine")
async def list_my_vouchers(request: Request, db: DbSession, customer: CurrentCustomer, store_id: int | None = None):
    """我的可用代金券。传 store_id 时仅返回该门店可用代金券。"""
    items = await CouponService.list_available_vouchers(db, customer.id, store_id=store_id)
    return success({"items": items}, request=request)


@router.get("/customers/me")
async def get_my_profile(request: Request, db: DbSession, customer: CurrentCustomer):
    """小程序会员中心：档案 + 等级 + 积分进度 + 人脸状态。"""
    profile = await CustomerService.build_member_profile(db, customer)
    channel = _customer_channel_from_request(request, "wechat")
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    need_prompt = not CustomerService.is_member(customer)
    from app.utils.birthday import (
        birthday_for_picker,
        format_birthday_display,
        normalize_calendar,
    )

    cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
    store_id = getattr(customer, "registered_store_id", None) or getattr(
        customer, "first_seen_store_id", None
    )
    benefit = await CustomerService.birthday_benefit_payload(
        db, int(store_id) if store_id else None, customer, try_issue_coupon=True
    )
    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "display_name": customer.display_name,
            "nickname": disp["nickname"],
            "avatar_url": disp["avatar_url"],
            "phone": customer.phone,
            "birthday": serialize_dt(customer.birthday),
            "birthday_picker": birthday_for_picker(customer.birthday, cal),
            "birthday_calendar": cal,
            "birthday_display": format_birthday_display(customer.birthday, cal),
            "birthday_locked": int(getattr(customer, "birthday_locked", 0) or 0) == 1,
            "birthday_benefit": benefit,
            "need_member_prompt": need_prompt,
            "is_guest": need_prompt,
            **profile,
            "face_status": await _face_status(db, customer.id),
        },
        request=request,
    )


@router.get("/customers/me/preferences")
async def get_my_preferences(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = None,
):
    """当前会员口味偏好（小程序专用，以令牌身份为准，避免本地 customer_id 过期）。"""
    prefs = await PreferenceService.get_recommendations(db, customer.id, store_id=store_id)
    result = await db.execute(
        select(CustomerPreference)
        .where(CustomerPreference.customer_id == customer.id)
        .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
    )
    prefs["all"] = [
        {
            "category": p.category,
            "option_code": p.option_code,
            "option_name": p.option_name or p.option_code,
            "score": float(p.preference_score),
            "order_count": p.order_count,
        }
        for p in result.scalars()
    ]
    return success(prefs, request=request)


@router.get("/customers/me/face-status")
async def get_my_face_status(request: Request, db: DbSession, customer: CurrentCustomer):
    return success(await _face_status(db, customer.id), request=request)


@router.get("/customers/me/privacy-settings")
async def get_my_privacy_settings(request: Request, db: DbSession, customer: CurrentCustomer):
    """个性化推荐开关 + 同意版本/时间（顾客隐私入口）。"""
    from app.services.privacy_service import PrivacyService

    return success(
        await PrivacyService.get_privacy_settings(db, customer),
        request=request,
    )


class PrivacySettingsPatch(BaseModel):
    recommend_opt_in: int = Field(..., ge=0, le=1, description="0=关闭个性化推荐，1=开启")


@router.put("/customers/me/privacy-settings")
async def update_my_privacy_settings(
    payload: PrivacySettingsPatch,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """更新个性化推荐开关（写审计）。关闭后端 ingest 立即拒收，前端 tracker 亦停止上报。"""
    from app.services.privacy_service import PrivacyService

    result = await PrivacyService.update_opt_in(
        db, customer, int(payload.recommend_opt_in), request=request
    )
    await db.commit()
    return success(result, request=request)


@router.get("/customers/me/taste-profile")
async def get_my_taste_profile(request: Request, db: DbSession, customer: CurrentCustomer):
    """顾客口味画像可读视图（按维度分组 + 规格偏好）。"""
    from app.services.privacy_service import PrivacyService

    return success(
        await PrivacyService.get_taste_profile_view(db, customer),
        request=request,
    )


@router.post("/customers/me/taste-profile/clear")
async def clear_my_taste_profile(request: Request, db: DbSession, customer: CurrentCustomer):
    """清除画像派生数据（行为事件/画像/评价/customer_preferences），订单本体保留；写审计。"""
    from app.services.privacy_service import PrivacyService

    result = await PrivacyService.clear_taste_profile(db, customer, request=request)
    await db.commit()
    return success(result, request=request)


# ── 积分兑换 / 明细 / 选项 ───────────────────────────────────────

POINT_ENTRY_TYPE_LABELS = {
    "EARN": "消费获得",
    "REFUND_DEDUCT": "退款扣减",
    "EXCHANGE": "兑换扣减",
    "EXPIRE": "过期清零",
}


@router.get("/customers/me/points/exchange-options")
async def get_my_exchange_options(
    request: Request, db: DbSession, customer: CurrentCustomer
):
    """小程序：积分兑换选项列表（仅启用的，含券摘要）。"""
    from app.models import PointExchangeOption
    from app.services.system_settings_service import SystemSettingsService

    enabled = await SystemSettingsService.effective_int(
        db, "points_exchange_enabled", 0, store_id=getattr(customer, "registered_store_id", None)
    )
    if not enabled:
        return success({"enabled": False, "items": []}, request=request)

    store_id = getattr(customer, "registered_store_id", None)
    q = (
        select(PointExchangeOption)
        .where(
            PointExchangeOption.status == 1,
            or_(PointExchangeOption.store_id.is_(None), PointExchangeOption.store_id == int(store_id))
            if store_id
            else PointExchangeOption.store_id.is_(None),
        )
        .order_by(PointExchangeOption.sort_order, PointExchangeOption.id)
    )
    rows = list((await db.execute(q)).scalars().all())
    items = []
    for opt in rows:
        tmpl = await db.get(CouponTemplate, int(opt.template_id))
        if not tmpl or int(tmpl.status) != 1:
            continue
        items.append(
            {
                "id": opt.id,
                "name": opt.name,
                "points_cost": int(opt.points_cost),
                "store_scope": "HQ" if not opt.store_id else "STORE",
                "coupon_summary": CouponService.summary_label(tmpl),
                "coupon_name": tmpl.name,
            }
        )
    return success({"enabled": True, "items": items}, request=request)


class PointsExchangeBody(BaseModel):
    option_id: int = Field(..., gt=0)


@router.post("/customers/me/points/exchange")
async def exchange_my_points(
    body: PointsExchangeBody, request: Request, db: DbSession, customer: CurrentCustomer
):
    """小程序：用积分兑换优惠券（同月同选项幂等，不重复扣分）。"""
    result = await CustomerService.exchange_points_for_coupon(
        db, int(customer.id), int(body.option_id)
    )
    await db.commit()
    return success(result, message="兑换成功" if not result.get("already_exchanged") else "本期已兑换", request=request)


@router.get("/customers/me/points/ledger")
async def get_my_points_ledger(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 20,
    entry_type: str | None = None,
):
    """小程序：积分明细（真分页，按 created_at DESC + id DESC 兜底）。"""
    from app.models import PointLedger
    from app.core.response import calc_pages

    conds = [PointLedger.customer_id == int(customer.id)]
    if entry_type:
        conds.append(PointLedger.entry_type == entry_type)
    q = select(PointLedger).where(*conds).order_by(PointLedger.created_at.desc(), PointLedger.id.desc())
    total = await db.scalar(
        select(func.count()).select_from(PointLedger).where(*conds)
    ) or 0
    rows = list(
        (
            await db.execute(
                q.offset(max(0, (page - 1) * page_size)).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": r.id,
            "delta": r.delta,
            "balance_after": r.balance_after,
            "entry_type": r.entry_type,
            "entry_type_label": POINT_ENTRY_TYPE_LABELS.get(r.entry_type, r.entry_type),
            "order_id": r.order_id,
            "created_at": serialize_dt(r.created_at),
        }
        for r in rows
    ]
    return success(
        {
            "items": items,
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "pages": calc_pages(int(total), page_size),
        },
        request=request,
    )


