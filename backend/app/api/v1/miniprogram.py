"""小程序登录与扫码点餐入口"""
import asyncio
import base64
import re

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, File, Request, UploadFile

from app.api.deps import CurrentCustomer, DbSession, OptionalCustomer
from app.core.cache_keys import cart_fingerprint
from app.core.security import create_customer_token, decode_customer_token
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    DiningTable,
    FaceProfile,
    Order,
    Store,
)
from app.schemas.reco import BehaviorEventBatch, FeedbackBatchIn
from app.services.audit_service import AuditService
from app.services.behavior_event_service import MAX_BATCH, ingest_events
from app.services.customer_service import CustomerService, PreferenceService
from app.services.feedback_service import EVALUABLE_PAYMENT_STATUSES, FeedbackService
from app.services.coupon_service import CouponService
from app.services.dining_session_service import DiningSessionService
from app.services.face_extractor import extract_face_embedding_with_quality
from app.services.face_service import FaceService
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth
from app.services.ab_service import DEFAULT_EXPERIMENT_KEY, AbService
from app.services.reco_cache import get_cached_reco, set_cached_reco
from app.services.recommendation_service import RecommendationService
from app.utils.upload import fetch_and_save_remote_image, read_upload_limited, save_upload

router = APIRouter(prefix="/miniprogram", tags=["小程序"])


def _issue_customer_token(customer: Customer, channel: str = "wechat") -> str:
    return create_customer_token(
        customer.id,
        channel,
        token_version=int(getattr(customer, "token_version", 0) or 0),
    )


def _customer_channel_from_request(
    request: Request,
    default: str = "wechat",
    body_channel: str | None = None,
) -> str:
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            ch = str(decode_customer_token(auth_header.split(" ", 1)[1]).get("channel") or "").strip().lower()
            if ch in ("wechat", "alipay"):
                return ch
        except Exception:
            pass
    body = str(body_channel or "").strip().lower()
    if body in ("wechat", "alipay"):
        return body
    if default in ("wechat", "alipay"):
        return default
    return "wechat"


async def _channel_login_payload(
    db,
    customer: Customer,
    channel: str,
    *,
    is_mock: bool,
    table,
    dining_session=None,
) -> dict:
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    is_member = CustomerService.is_member(customer)
    # 非阻塞引导：游客可提示开启会员，但绝不拦截点餐
    need_prompt = not is_member
    return {
        "customer_id": customer.id,
        "customer_no": customer.customer_no,
        "customer_token": _issue_customer_token(customer, channel),
        "nickname": disp["nickname"],
        "avatar_url": disp["avatar_url"],
        "phone": customer.phone,
        "is_new": not bool(getattr(customer, "first_seen_store_id", None)),
        "is_guest": not is_member,
        "member_status": str(getattr(customer, "member_status", None) or "GUEST").upper(),
        "account_status": str(getattr(customer, "account_status", None) or "ACTIVE").upper(),
        "profile_status": str(getattr(customer, "profile_status", None) or "EMPTY").upper(),
        "phone_verified": bool(int(getattr(customer, "phone_verified", 0) or 0)),
        # 兼容旧前端字段：仅表示「可软引导开通会员」，不表示必须注册才能点餐
        "need_member_prompt": need_prompt,
        "face_status": await _face_status(db, customer.id),
        "table": table,
        "dining_session": DiningSessionService._session_payload(dining_session),
        "is_mock": bool(is_mock),
    }


def _persistable_avatar_url(url: str | None) -> str | None:
    """只接受已上传到本系统的头像路径，拒绝小程序本地临时路径。"""
    if not url:
        return None
    u = str(url).strip()
    if not u:
        return None
    if u.startswith("/uploads/"):
        return u.split("?", 1)[0]
    marker = "/uploads/"
    if marker in u and (u.startswith("http://") or u.startswith("https://")):
        return u[u.find(marker) :].split("?", 1)[0]
    return None


class WechatMiniLoginRequest(BaseModel):
    code: str = Field(..., description="wx.login 返回的 code")
    nickname: str | None = None
    avatar_url: str | None = None
    table_token: str | None = None


class AlipayMiniLoginRequest(BaseModel):
    auth_code: str = Field(..., description="my.getAuthCode 返回的 authCode")
    nickname: str | None = None
    avatar_url: str | None = None
    table_token: str | None = None


class MemberRegisterRequest(BaseModel):
    # 安全约束：注册/激活不接受手机号。手机号只能走 /member/phone 官方一键授权，
    # 防止用户手输他人号码污染资料或冒享会员权益。
    nickname: str | None = None
    avatar_url: str | None = None
    enable_face: bool = False
    channel: str | None = None
    table_token: str | None = None


class MemberActivateRequest(BaseModel):
    """主动开通智能会员（GUEST → MEMBER，幂等）。"""
    nickname: str | None = None
    avatar_url: str | None = None
    table_token: str | None = None
    channel: str | None = None
    enable_face: bool = False


class MemberProfilePatchRequest(BaseModel):
    """仅修改资料，不触发会员激活/发券。"""
    nickname: str | None = None
    avatar_url: str | None = None
    channel: str | None = None


class PhoneBindRequest(BaseModel):
    phone: str | None = Field(
        default=None,
        min_length=11,
        max_length=11,
        pattern=r"^1\d{10}$",
        description="已废弃：手输渠道已关闭，服务端将拒绝；仅保留字段以返回明确错误提示",
    )
    phone_code: str | None = Field(default=None, description="微信 getPhoneNumber 返回的 code")
    alipay_phone_encrypted: str | None = Field(default=None, description="支付宝 my.getPhoneNumber 返回的 response 密文")
    channel: str | None = None
    confirm_merge: bool = Field(default=False, description="官方手机号已关联其他账号时，用户是否确认合并")


@router.get("/config")
async def miniprogram_config(request: Request, db: DbSession):
    """小程序前端配置（订阅模板、隐私能力开关等，不含密钥）。"""
    from app.services.system_settings_service import SystemSettingsService

    settings = get_settings()
    await SystemSettingsService.ensure_seeded(db)
    tmpl = (
        await SystemSettingsService.get_value(
            db,
            "wechat_subscribe_template_order_ready",
            settings.wechat_subscribe_template_order_ready or "",
        )
    ).strip()

    async def _flag(key: str, fallback: bool) -> bool:
        return await SystemSettingsService.get_bool(db, key, fallback)

    return success(
        {
            "subscribe_templates": {
                "order_ready": tmpl or None,
            },
            # 优先 system_settings，种子默认值来自 .env ENABLE_* 
            "enable_alipay_avatar": await _flag("enable_alipay_avatar", settings.enable_alipay_avatar),
            "enable_alipay_nickname": await _flag("enable_alipay_nickname", settings.enable_alipay_nickname),
            "enable_alipay_phone_bind": await _flag(
                "enable_alipay_phone_bind", settings.enable_alipay_phone_bind
            ),
            "enable_wx_avatar": await _flag("enable_wx_avatar", settings.enable_wx_avatar),
            "enable_wx_nickname": await _flag("enable_wx_nickname", settings.enable_wx_nickname),
            "enable_wx_phone_bind": await _flag("enable_wx_phone_bind", settings.enable_wx_phone_bind),
            "show_menu_search": await _flag("show_menu_search", settings.show_menu_search),
            "show_menu_smart_taste": await _flag(
                "show_menu_smart_taste", settings.show_menu_smart_taste
            ),
            "show_menu_guess_you_like": await _flag(
                "show_menu_guess_you_like", settings.show_menu_guess_you_like
            ),
            "hide_member_empty_wallet": await _flag(
                "hide_member_empty_wallet", settings.hide_member_empty_wallet
            ),
        },
        request=request,
    )


@router.get("/tables")
async def list_dine_tables(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = None,
):
    """堂食选桌（须指定门店；生产不返回完整 qr_token）。"""
    from app.core.config import get_settings
    from app.core.exceptions import AppException

    if not store_id:
        raise AppException(40051, "缺少 store_id，请先扫码进入门店")

    settings = get_settings()
    q = (
        select(DiningTable)
        .where(DiningTable.status == 1, DiningTable.store_id == int(store_id))
        .order_by(DiningTable.table_no)
    )
    rows = (await db.execute(q)).scalars().all()
    # 外带虚拟桌（table_no=WM）不参与堂食选桌
    rows = [
        t
        for t in rows
        if str(t.table_no or "").strip().upper() not in ("WM", "TKW", "TAKEAWAY")
        and not re.search(r"外卖|外带自取", str(t.table_name or ""))
    ]
    expose_token = settings.is_development
    return success(
        [
            {
                "id": t.id,
                "table_no": t.table_no,
                "table_name": t.table_name,
                "store_id": t.store_id,
                **({"qr_token": t.qr_token} if expose_token else {}),
            }
            for t in rows
        ],
        request=request,
    )


@router.post("/tables/bind")
async def bind_dine_table(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """选桌绑定：须凭扫码 qr_token 凭证，验证同门店后才允许绑定目标 table_id。

    P1 修复：原逻辑凭 body 中自报 table_id+store_id 即返回永久 qr_token，
    任何登录顾客可枚举 table_id 获取任意桌台 token。现要求传 qr_token（扫码获得），
    后端校验 qr_token 对应桌台与目标 table_id 同门店才允许绑定。
    """
    from app.core.config import get_settings
    from app.core.exceptions import AppException
    from app.services.table_resolve import resolve_dining_table

    body = await request.json()
    table_id = int(body.get("table_id") or 0)
    qr_token = (body.get("qr_token") or body.get("token") or "").strip()
    req_store = body.get("store_id")
    table = await db.get(DiningTable, table_id)
    if not table or table.status != 1:
        return success(None, message="桌台不存在或已禁用", request=request)

    settings = get_settings()
    # 生产必须带 qr_token 凭证；开发可省略但传了仍校验
    if not qr_token and not settings.is_development:
        raise AppException(40050, "请先扫码进入门店后再选桌")
    if qr_token:
        # 校验 qr_token 凭证：必须能解析到桌台，且与目标桌台同门店
        cred_table = await resolve_dining_table(db, qr_token)
        if not cred_table:
            raise AppException(40050, "扫码凭证无效，请重新扫码")
        if int(cred_table.store_id) != int(table.store_id):
            raise AppException(40052, "桌台不属于当前门店")
    else:
        # 开发环境兜底：须带 store_id 且一致
        if req_store is None:
            raise AppException(40050, "请先扫码进入门店后再选桌")
        if int(req_store) != int(table.store_id):
            raise AppException(40052, "桌台不属于当前门店")

    session = await DiningSessionService.start_or_refresh(
        db,
        customer_id=customer.id,
        table=table,
        table_token=table.qr_token,
    )
    await CustomerService.remember_first_seen(db, customer, table.qr_token, table=table)

    await AuditService.log(
        db,
        action="customer.table.bind",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "store_id": int(table.store_id) if table.store_id else None,
            "table_id": int(table.id),
            "customer_id": int(customer.id),
            "verified_by_token": bool(qr_token),
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(table.store_id) if table.store_id else None,
    )
    return success(
        {
            "id": table.id,
            "table_no": table.table_no,
            "table_name": table.table_name,
            "store_id": table.store_id,
            "qr_token": table.qr_token,
            "dining_session": DiningSessionService._session_payload(session),
        },
        request=request,
    )


@router.get("/entry")
async def scan_entry(
    request: Request,
    db: DbSession,
    token: str,
    channel: str = "wechat",
    store_id: int | None = None,
):
    from app.services.table_resolve import resolve_dining_table

    table = await resolve_dining_table(db, token, store_id=store_id)
    if not table:
        return success(None, message="无效桌台二维码", request=request)

    store = await _store_brief(db, table.store_id)
    return success(
        {
            "table": {
                "id": table.id,
                "table_no": table.table_no,
                "table_name": table.table_name,
                "store_id": table.store_id,
                "qr_token": table.qr_token,
            },
            "store": store,
            "channel": channel,
            "prompts": {
                # 首次扫码不强制注册；会员引导改到支付成功页等非阻塞时机
                "ask_register_member": False,
                "ask_face_welcome": False,
                "face_consent_text": "开启人脸迎宾后到店可自动欢迎。当前为 basic anti-spoof（清晰度/亮度粗检，非真活体），可随时关闭。",
                "member_consent_text": "开启智能会员后系统会记住您的口味爱好，支持一键复购；可领取欢迎券并享受会员等级权益。随时可在「我的」页开通，不影响点餐。",
            },
        },
        request=request,
    )


@router.get("/scan-landing")
async def scan_landing(
    request: Request,
    db: DbSession,
    token: str,
):
    """一码通扫 H5 落地页：校验桌台并返回微信/支付宝跳转链接（公开接口）。"""
    from app.services.qr_links import build_table_qr_links
    from app.services.table_resolve import resolve_dining_table

    table = await resolve_dining_table(db, token)
    if not table:
        return success(None, message="无效桌台二维码", request=request)

    store = await _store_brief(db, table.store_id)
    links = build_table_qr_links(table.qr_token)
    from app.services.wechat_mini_client import generate_url_link

    wechat_url_link, wechat_url_link_error = await generate_url_link(
        path="pages/index/index",
        query=f"token={table.qr_token}&channel=wechat",
        env_version=links.get("mini_program_env_version"),
    )
    return success(
        {
            "table": {
                "id": table.id,
                "table_no": table.table_no,
                "table_name": table.table_name,
                "store_id": table.store_id,
            },
            "store": store,
            "wechat_scheme": links["wechat_scheme"],
            "wechat_url_link": wechat_url_link,
            "wechat_url_link_error": wechat_url_link_error,
            "alipay_scheme": links["alipay_scheme"],
            "h5_order_url": links["h5_order_url"],
            "scan_url": links["scan_url"],
            "mini_program_env_version": links.get("mini_program_env_version"),
            "wechat_app_configured": bool(links.get("wechat_app_id") and links["wechat_app_id"] != "YOUR_WECHAT_APPID"),
            "alipay_app_configured": bool(links.get("alipay_app_id") and links["alipay_app_id"] != "YOUR_ALIPAY_APPID"),
        },
        request=request,
    )


@router.get("/wechat-debug-qrcode")
async def wechat_debug_qrcode(
    request: Request,
    db: DbSession,
    token: str,
):
    """本地调试：返回微信小程序码 PNG（扫码进 develop/trial/release，不走 weixin://）。"""
    from fastapi.responses import Response

    from app.core.config import get_settings
    from app.services.table_resolve import resolve_dining_table
    from app.services.wechat_mini_client import generate_wxacode

    table = await resolve_dining_table(db, token)
    if not table:
        raise AppException(40403, "无效桌台二维码")
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    png, err = await generate_wxacode(
        path="pages/index/index",
        query=f"token={table.qr_token}&channel=wechat",
        env_version=SystemSettingsService.resolved_mini_program_env_version_sync(),
        width=280,
    )
    if not png:
        raise AppException(50034, err or "生成微信小程序码失败")
    try:
        from app.utils.logo_overlay import overlay_center_logo

        png = overlay_center_logo(png, ratio=0.30)
    except Exception:
        # Logo 叠加失败时仍返回官方小程序码
        pass
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/wechat/login")
async def wechat_mini_login(data: WechatMiniLoginRequest, request: Request, db: DbSession):
    auth = WechatMiniAuth()
    session = await auth.code2session(data.code)
    customer = await CustomerService.get_or_create_by_wechat(db, session["openid"], session.get("unionid"))
    acc = (
        await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == session["openid"]))
    ).scalar_one_or_none()
    # 登录不接受客户端缓存写回新客；资料完善走 /member/profile 或 /member/activate
    profile_ready = bool(acc) and int(getattr(acc, "profile_completed", 0) or 0) == 1
    if profile_ready:
        await CustomerService.save_channel_profile(
            db,
            customer.id,
            "wechat",
            nickname=data.nickname,
            avatar_url=_persistable_avatar_url(data.avatar_url),
        )
        CustomerService.fill_customer_display_if_empty(
            customer,
            nickname=data.nickname,
            avatar_url=_persistable_avatar_url(data.avatar_url),
        )
        CustomerService.refresh_profile_status(customer)
        await db.flush()

    table_record = await _resolve_table_record(db, data.table_token) if data.table_token else None
    table = await _table_payload(db, table_record)
    await CustomerService.remember_first_seen(db, customer, data.table_token, table=table_record)
    dining_session = None
    if table_record:
        dining_session = await DiningSessionService.start_or_refresh(
            db,
            customer_id=customer.id,
            table=table_record,
            table_token=data.table_token or table_record.qr_token,
        )
    payload = await _channel_login_payload(
        db,
        customer,
        "wechat",
        is_mock=bool(session.get("is_mock")),
        table=table,
        dining_session=dining_session,
    )
    return success(payload, request=request)


@router.post("/alipay/login")
async def alipay_mini_login(data: AlipayMiniLoginRequest, request: Request, db: DbSession):
    auth = AlipayMiniAuth()
    session = await auth.auth_code2user(data.auth_code)
    customer = await CustomerService.get_or_create_by_alipay(db, session["alipay_user_id"])
    acc = (
        await db.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == session["alipay_user_id"])
        )
    ).scalar_one_or_none()
    # 登录不接受客户端缓存写回新客；资料完善走 /member/profile 或 /member/activate
    profile_ready = bool(acc) and int(getattr(acc, "profile_completed", 0) or 0) == 1
    if profile_ready:
        await CustomerService.save_channel_profile(
            db,
            customer.id,
            "alipay",
            nickname=data.nickname,
            avatar_url=_persistable_avatar_url(data.avatar_url),
        )
        CustomerService.fill_customer_display_if_empty(
            customer,
            nickname=data.nickname,
            avatar_url=_persistable_avatar_url(data.avatar_url),
        )
        CustomerService.refresh_profile_status(customer)
        await db.flush()

    table_record = await _resolve_table_record(db, data.table_token) if data.table_token else None
    table = await _table_payload(db, table_record)
    await CustomerService.remember_first_seen(db, customer, data.table_token, table=table_record)
    dining_session = None
    if table_record:
        dining_session = await DiningSessionService.start_or_refresh(
            db,
            customer_id=customer.id,
            table=table_record,
            table_token=data.table_token or table_record.qr_token,
        )
    payload = await _channel_login_payload(
        db,
        customer,
        "alipay",
        is_mock=bool(session.get("is_mock")),
        table=table,
        dining_session=dining_session,
    )
    return success(payload, request=request)


@router.post("/member/register")
async def register_member(
    data: MemberRegisterRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """兼容旧接口：等价于主动开通会员（activate）+ 可选写资料。"""
    channel = _customer_channel_from_request(request, "wechat", body_channel=data.channel)
    avatar = _persistable_avatar_url(data.avatar_url)
    customer, newly = await CustomerService.activate_member(
        db,
        customer,
        channel=channel,
        nickname=data.nickname,
        avatar_url=avatar,
        table_token=data.table_token,
        mark_channel_completed=True,
    )
    await AuditService.log(
        db,
        action="customer.member.register",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "customer_id": int(customer.id),
            "channel": channel,
            "newly_activated": bool(newly),
            "store_id": int(getattr(customer, "registered_store_id", 0) or 0) or None,
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(getattr(customer, "registered_store_id", 0) or 0) or None,
    )
    return await _member_activation_response(
        db,
        customer,
        channel=channel,
        newly_activated=newly,
        nickname=data.nickname,
        avatar=avatar,
        enable_face=data.enable_face,
        request=request,
    )


async def _member_activation_response(
    db,
    customer: Customer,
    *,
    channel: str,
    newly_activated: bool,
    nickname: str | None,
    avatar: str | None,
    enable_face: bool,
    request: Request,
):
    issued = None
    if newly_activated:
        issued = await CouponService.issue_welcome_coupon(db, customer.id)
    await db.flush()
    profile = await CustomerService.build_member_profile(db, customer)
    welcome = await CouponService.welcome_coupon_payload(db, issued)
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    payload = {
        "customer_id": customer.id,
        "customer_no": customer.customer_no,
        "nickname": disp["nickname"] or nickname,
        "avatar_url": disp["avatar_url"] or avatar,
        "phone": customer.phone,
        "face_status": await _face_status(db, customer.id),
        "member_registered": CustomerService.is_member(customer),
        "member_activated": newly_activated,
        "need_member_prompt": not CustomerService.is_member(customer),
        **profile,
        "need_face_consent": bool(enable_face),
        "customer_token": _issue_customer_token(customer, channel),
        "welcome_coupon_issued": welcome is not None,
        "welcome_coupon": welcome,
        "welcome_coupon_name": await CouponService.welcome_template_name(db),
    }
    if enable_face:
        return success(payload, message="会员已开通，请完成摄像头人脸采集授权", request=request)
    if newly_activated and welcome:
        msg = f"已开启智能会员，赠送「{welcome.get('name') or await CouponService.welcome_template_name(db)}」"
    elif newly_activated:
        msg = "已开启智能会员"
    else:
        msg = "您已是会员"
    return success(payload, message=msg, request=request)


@router.post("/member/activate")
async def activate_member(
    data: MemberActivateRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """用户明确点击「开启智能会员」：幂等 GUEST→MEMBER，仅首次发欢迎券。"""
    channel = _customer_channel_from_request(request, "wechat", body_channel=data.channel)
    avatar = _persistable_avatar_url(data.avatar_url)
    customer, newly = await CustomerService.activate_member(
        db,
        customer,
        channel=channel,
        nickname=data.nickname,
        avatar_url=avatar,
        table_token=data.table_token,
        mark_channel_completed=True,
    )
    await AuditService.log(
        db,
        action="customer.member.activate",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "customer_id": int(customer.id),
            "channel": channel,
            "newly_activated": bool(newly),
            "store_id": int(getattr(customer, "registered_store_id", 0) or 0) or None,
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
        store_id=int(getattr(customer, "registered_store_id", 0) or 0) or None,
    )
    return await _member_activation_response(
        db,
        customer,
        channel=channel,
        newly_activated=newly,
        nickname=data.nickname,
        avatar=avatar,
        enable_face=data.enable_face,
        request=request,
    )


@router.patch("/member/profile")
async def patch_member_profile(
    data: MemberProfilePatchRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """修改昵称/头像，不触发会员激活与欢迎券。"""
    channel = _customer_channel_from_request(request, "wechat", body_channel=data.channel)
    avatar = _persistable_avatar_url(data.avatar_url)
    await CustomerService.save_channel_profile(
        db,
        customer.id,
        channel,
        nickname=data.nickname,
        avatar_url=avatar,
        completed=True if (data.nickname or avatar) else None,
    )
    CustomerService.fill_customer_display_if_empty(customer, nickname=data.nickname, avatar_url=avatar)
    # 主动更新头像时同步顾客表（后台顾客管理看的是 customers.avatar_url）
    if avatar:
        customer.avatar_url = avatar
    if data.nickname and CustomerService.is_meaningful_nickname(data.nickname):
        n = str(data.nickname).strip()
        customer.nickname = n
        if not customer.display_name:
            customer.display_name = n
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    profile = await CustomerService.build_member_profile(db, customer)
    await AuditService.log(
        db,
        action="customer.member.profile_update",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "customer_id": int(customer.id),
            "channel": channel,
            "fields": list(data.model_dump(exclude_unset=True).keys()),
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success(
        {
            "customer_id": customer.id,
            "nickname": disp["nickname"],
            "avatar_url": disp["avatar_url"],
            "phone": customer.phone,
            "need_member_prompt": not CustomerService.is_member(customer),
            **profile,
        },
        request=request,
    )


@router.post("/member/avatar")
async def upload_member_avatar(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    file: UploadFile = File(...),
):
    """小程序上传会员头像（微信/支付宝 chooseAvatar 仅返回本地临时路径，须先上传）。"""
    name = (file.filename or "").strip() or "avatar.jpg"
    if "." not in name:
        # 部分端上传无扩展名，按 jpeg 兜底（save_upload 会做魔数校验）
        ct = (file.content_type or "").lower()
        if "png" in ct:
            name = "avatar.png"
        elif "webp" in ct:
            name = "avatar.webp"
        elif "gif" in ct:
            name = "avatar.gif"
        else:
            name = "avatar.jpg"
        file.filename = name
    url = await save_upload(file, subdir="avatars")
    channel = _customer_channel_from_request(request, "wechat")
    # 游客也可上传头像（仅写资料，不激活会员）
    await CustomerService.save_channel_profile(db, customer.id, channel, avatar_url=url, completed=True)
    # 用户主动换头像：同步顾客表，供后台顾客管理展示
    customer.avatar_url = url
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    await AuditService.log(
        db,
        action="customer.member.avatar_upload",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={"customer_id": int(customer.id), "channel": channel, "avatar_url": url},
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success(
        {
            "avatar_url": url,
            "customer_id": customer.id,
        },
        request=request,
    )


class AvatarFromUrlRequest(BaseModel):
    source_url: str = Field(..., min_length=8, max_length=1024, description="支付宝/微信头像网络地址")


@router.post("/member/avatar-from-url")
async def import_member_avatar_from_url(
    data: AvatarFromUrlRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """chooseAvatar 在支付宝真机常返回 CDN 地址，无法作为 uploadFile 的 filePath，改由服务端拉取转存。"""
    url = await fetch_and_save_remote_image(data.source_url, subdir="avatars")
    channel = _customer_channel_from_request(request, "wechat")
    await CustomerService.save_channel_profile(db, customer.id, channel, avatar_url=url, completed=True)
    customer.avatar_url = url
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    await AuditService.log(
        db,
        action="customer.member.avatar_import",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={"customer_id": int(customer.id), "channel": channel, "avatar_url": url},
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success(
        {
            "avatar_url": url,
            "customer_id": customer.id,
        },
        request=request,
    )


# 调试环境模拟绑定的固定测试手机号（明显占位、非真实分配号码段内常用示例）
_MOCK_DEBUG_PHONE = "13800000000"


async def _save_unverified_phone_response(
    db: AsyncSession,
    request: Request,
    customer,  # Customer
    channel: str,
    phone: str,
    *,
    source: str,
    message: str,
):
    """调试链路共用：保存未验证手机号（不合并账号），返回与官方授权同构的响应。"""
    customer = await CustomerService.save_unverified_phone(db, customer, phone)
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    profile = await CustomerService.build_member_profile(db, customer)
    await AuditService.log(
        db,
        action="customer.member.phone_bind",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "customer_id": int(customer.id),
            "channel": channel,
            "phone_verified": False,
            "account_merged": False,
            "verification_source": source,
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success(
        {
            "phone": customer.phone,
            "customer_id": customer.id,
            "customer_token": _issue_customer_token(customer, channel),
            "phone_verified": False,
            "account_merged": False,
            "need_member_prompt": not CustomerService.is_member(customer),
            "welcome_coupon_issued": False,
            "welcome_coupon": None,
            "welcome_coupon_name": await CouponService.welcome_template_name(db),
            **profile,
        },
        message=message,
        request=request,
    )


@router.post("/member/phone")
async def bind_member_phone(
    data: PhoneBindRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """绑定/更新会员手机号（可合并历史账号）。"""
    import re

    from app.services.alipay_mini_client import decrypt_phone_response
    from app.services.wechat_mini_client import get_phone_by_code

    channel = _customer_channel_from_request(request, "wechat", body_channel=data.channel)
    settings = get_settings()

    # ── 调试环境：允许手输手机号（仅开发/测试）──
    # 保存为未验证手机号（phone_verified=0），不合并账号、不享受与已验证手机号挂钩的权益，
    # 仅方便个人主体/未开通官方取号能力时调试出餐提醒等流程。生产环境一律走官方授权。
    if data.phone:
        if not settings.is_development:
            raise AppException(
                40054,
                "为防止手机号被冒用，仅支持微信/支付宝官方一键授权绑定手机号，不支持手动输入",
            )
        if not re.fullmatch(r"1\d{10}", data.phone):
            raise AppException(40053, "手机号格式不正确")
        return await _save_unverified_phone_response(
            db, request, customer, channel, data.phone,
            source="MANUAL_DEV",
            message="调试环境：手机号已保存（未验证），生产环境须使用官方一键授权",
        )

    supplied = sum(
        bool(value)
        for value in (data.phone_code, data.alipay_phone_encrypted)
    )
    if supplied != 1:
        raise AppException(40054, "微信 phone_code、支付宝授权密文必须且只能提供一种")

    # ── 调试环境：mock 一键授权凭据（与生产同一按钮流程，后端返回固定测试号）──
    # 个人主体/未开通取号能力时，真机无法弹出官方授权窗，前端提交 mock:debug 凭据
    # 走完整绑定链路（未验证、不合并账号），便于本地/真机调试。
    if data.phone_code == "mock:debug" or data.alipay_phone_encrypted == "mock:debug":
        if not settings.is_development:
            raise AppException(40054, "mock 授权凭据仅限开发调试环境使用")
        return await _save_unverified_phone_response(
            db, request, customer, channel,
            _MOCK_DEBUG_PHONE,
            source="MOCK_DEV",
            message="调试环境：已模拟绑定测试手机号（未验证），生产环境将走官方授权",
        )

    verified_source: str
    if data.phone_code:
        if channel == "alipay":
            raise AppException(40054, "支付宝端请使用 alipay_phone_encrypted 字段")
        raw_phone = await get_phone_by_code(data.phone_code)
        verified_source = "WECHAT"
    else:
        if channel != "alipay":
            raise AppException(40054, "微信端不能提交支付宝手机号授权数据")
        raw_phone = decrypt_phone_response(data.alipay_phone_encrypted)
        verified_source = "ALIPAY"
    if not raw_phone:
        raise AppException(40054, "未获取到授权手机号，请重新授权")
    if not re.fullmatch(r"1\d{10}", raw_phone):
        raise AppException(40053, "手机号格式不正确")
    customer, merged = await CustomerService.bind_verified_phone(
        db,
        customer,
        raw_phone,
        verification_source=verified_source,
        confirm_merge=data.confirm_merge,
        request=request,
    )
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    # 绑手机不发欢迎券；欢迎券仅在主动开通会员时发放
    profile = await CustomerService.build_member_profile(db, customer)
    await AuditService.log(
        db,
        action="customer.member.phone_bind",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={
            "customer_id": int(customer.id),
            "channel": channel,
            "phone_verified": bool(int(getattr(customer, "phone_verified", 0) or 0)),
            "account_merged": bool(merged),
            "verification_source": verified_source,
        },
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success(
        {
            "phone": customer.phone,
            "customer_id": customer.id,
            "customer_token": _issue_customer_token(customer, channel),
            "phone_verified": bool(int(getattr(customer, "phone_verified", 0) or 0)),
            "account_merged": merged,
            "need_member_prompt": not CustomerService.is_member(customer),
            "welcome_coupon_issued": False,
            "welcome_coupon": None,
            "welcome_coupon_name": await CouponService.welcome_template_name(db),
            **profile,
        },
        message=(
            "账号已安全合并，手机号验证成功"
            if merged
            else "手机号验证成功"
        ),
        request=request,
    )


@router.get("/stores/{store_id}")
async def get_public_store(store_id: int, request: Request, db: DbSession):
    """小程序公开门店信息（无需登录）。"""
    store = await _store_brief(db, store_id)
    if not store:
        raise AppException(40405, "门店不存在或已停业")
    return success(store, request=request)


@router.post("/face/consent")
async def consent_face(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    image: UploadFile = File(...),
):
    """顾客主动授权：摄像头实拍后上传，服务端质量+basic anti-spoof 校验并提取特征，禁止客户端伪造向量"""
    raw = await read_upload_limited(image)
    if not raw:
        raise AppException(40030, "空图片")

    try:
        feature, quality = await asyncio.to_thread(extract_face_embedding_with_quality, raw)
    except ValueError as e:
        raise AppException(40031, str(e)) from e

    profile = await FaceService.register_face(db, customer.id, feature)
    from app.services.audit_service import AuditService

    await AuditService.log_face_consent(
        db,
        customer_id=customer.id,
        profile_id=profile.id,
        request=request,
        extra={"via": "upload", "anti_spoof_level": "basic"},
    )
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "anti_spoof_level": "basic",
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
                "anti_spoof_level": quality.get("anti_spoof_level", "basic"),
            },
        },
        request=request,
    )


@router.post("/face/consent-base64")
async def consent_face_base64(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """小程序摄像头 takePhoto 后的 base64 授权（禁止相册选图 / 禁止客户端上传 embedding）"""
    body = await request.json()
    if body.get("feature") is not None or body.get("embedding") is not None or body.get("feature_vector") is not None:
        raise AppException(40036, "禁止客户端上传人脸特征向量，仅接受摄像头实拍图片")
    b64 = body.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        raise AppException(40032, "缺少 image_base64")
    # 占位/过短串直接拒绝（如 H5 假数据）
    if len(b64) < 800:
        raise AppException(40033, "人脸图像无效，请使用小程序摄像头实时采集")
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    mb = await SystemSettingsService.get_int(db, "max_upload_size_mb", settings.max_upload_size_mb)
    max_b64 = mb * 1024 * 1024 * 2  # base64 膨胀约 4/3，再留余量
    if len(b64) > max_b64:
        raise AppException(40030, f"图片超过 {mb}MB")
    try:
        raw = base64.b64decode(b64, validate=False)
        max_size = mb * 1024 * 1024
        if len(raw) > max_size:
            raise AppException(40030, f"图片超过 {mb}MB")
        if len(raw) < 1024:
            raise AppException(40034, "人脸图像过小，请重新正对摄像头采集")
        feature, quality = await asyncio.to_thread(extract_face_embedding_with_quality, raw)
    except AppException:
        raise
    except ValueError as e:
        raise AppException(40031, str(e)) from e
    except Exception as e:
        raise AppException(40035, f"人脸采集失败: {e}") from e

    profile = await FaceService.register_face(db, customer.id, feature)
    from app.services.audit_service import AuditService

    await AuditService.log_face_consent(
        db,
        customer_id=customer.id,
        profile_id=profile.id,
        request=request,
        extra={"via": "base64", "anti_spoof_level": "basic"},
    )
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "anti_spoof_level": "basic",
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
                "anti_spoof_level": quality.get("anti_spoof_level", "basic"),
            },
        },
        request=request,
    )


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    n = await FaceService.revoke_face(db, customer.id, request=request)
    return success({"revoked": True, "count": n}, request=request)


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


@router.get("/member/levels")
async def list_member_levels(request: Request, db: DbSession):
    """会员等级说明（公开，不含顾客隐私）。"""
    levels = await CustomerService.list_levels_ordered(db)
    return success(
        {
            "points_rule": "实付1元=1积分，积分达标自动升级享对应折扣",
            "levels": [
                {
                    "id": lv.id,
                    "name": lv.name,
                    "min_points": int(lv.min_points),
                    "discount_rate": str(lv.discount_rate),
                    "discount_text": CustomerService.discount_display_text(lv.discount_rate),
                }
                for lv in levels
            ],
        },
        request=request,
    )


@router.get("/customers/me")
async def get_my_profile(request: Request, db: DbSession, customer: CurrentCustomer):
    """小程序会员中心：档案 + 等级 + 积分进度 + 人脸状态。"""
    profile = await CustomerService.build_member_profile(db, customer)
    channel = _customer_channel_from_request(request, "wechat")
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    need_prompt = not CustomerService.is_member(customer)
    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "nickname": disp["nickname"],
            "avatar_url": disp["avatar_url"],
            "phone": customer.phone,
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


class PrivacyConsentRequest(BaseModel):
    version: str | None = Field(default=None, max_length=40, description="同意版本号")


@router.post("/privacy/consent")
async def record_privacy_consent(
    payload: PrivacyConsentRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """记录版本化同意留痕：写 consent_version / recommend_consent_at / recommend_opt_in=1。"""
    from app.services.privacy_service import PrivacyService

    result = await PrivacyService.record_consent(
        db, customer, version=payload.version, request=request
    )
    await db.commit()
    return success(result, request=request)


@router.post("/events")
async def submit_behavior_events(
    data: BehaviorEventBatch,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """顾客隐式行为批量上报（≤50 条）：event_id 幂等，关闭个性化时直接丢弃。"""
    if len(data.events) > MAX_BATCH:
        raise AppException(40001, f"单批最多 {MAX_BATCH} 条事件")
    result = await ingest_events(db, customer, [e.model_dump() for e in data.events])
    return success(result, request=request)


@router.get("/menu")
async def get_menu_bundle_api(
    request: Request,
    db: DbSession,
    store_id: int,
    version: int | None = None,
):
    """菜单聚合（分类 + 在售商品一次成型），带版本戳的轻量校验。

    客户端携带 version 且与服务端一致时仅返回 changed:false（小响应、不查库组装）；
    否则返回完整 bundle 与新版本。Redis 不可用时降级为每次查库。
    """
    if not store_id:
        raise AppException(40001, "store_id 必填")
    sid = int(store_id)

    from app.core.menu_cache import get_menu_version
    from app.services.menu_service import get_or_build_menu_bundle

    current_version = await get_menu_version(sid)
    if version is not None and current_version and int(version) == int(current_version):
        return success({"changed": False, "version": current_version}, request=request)

    bundle = await get_or_build_menu_bundle(db, sid)
    return success(
        {
            "changed": True,
            "version": int(bundle.get("version") or current_version or 0),
            "categories": bundle["categories"],
            "products": bundle["products"],
        },
        request=request,
    )


_RECO_SCENES = {"recommend_menu", "settle_addon"}


def _serialize_reco(ranked, state, meta, ab_bucket, ab_status=None):
    items = []
    for r in ranked:
        p = r.product
        badges = []
        if p.is_hot:
            badges.append("hot")
        if r.scores.get("novel", 0) >= 0.6:
            badges.append("new")
        if p.is_recommended:
            badges.append("staff")
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "price": f"{p.price:.2f}",
                "main_image": p.main_image,
                "category_id": p.category_id,
                "category_name": p.category_name,
                "taste_tags": list(p.tags),
                "badges": badges,
                "reason": r.reason,
            }
        )
    return {
        "state": state,
        "items": items,
        "meta": meta,
        "ab": {"experiment": DEFAULT_EXPERIMENT_KEY, "bucket": ab_bucket, "status": ab_status},
    }


@router.get("/recommendations")
async def get_recommendations(
    request: Request,
    db: DbSession,
    customer: OptionalCustomer,
    store_id: int,
    scene: str = "recommend_menu",
    limit: int = 10,
    cart_item_ids: str | None = None,
):
    """智能推荐（未登录/关闭个性化走冷启动热销+新品）。结果 Redis 缓存 ≤120s。"""
    if not store_id:
        raise AppException(40001, "store_id 必填")
    if scene not in _RECO_SCENES:
        raise AppException(40001, "非法推荐场景")
    limit = max(1, min(int(limit or 10), 20))

    exclude_ids = []
    for raw in (cart_item_ids or "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            exclude_ids.append(int(raw))

    ab_bucket = "v1"
    ab_status = None
    weight_override = None
    fingerprint = cart_fingerprint(exclude_ids) if scene == "settle_addon" else ""
    cid = customer.id if customer else None

    if cid is not None:
        ab_bucket, ab_params, ab_exp = await AbService.assign(db, cid, DEFAULT_EXPERIMENT_KEY)
        if ab_exp is not None:
            ab_status = ab_exp.status
        weight_override = (ab_params or {}).get("reco_weights")

    cached = await get_cached_reco(cid, store_id, scene, ab_bucket, fingerprint)
    if cached is not None:
        return success(cached, request=request)

    ranked, state, meta = await RecommendationService.recommend(
        db,
        store_id,
        customer_id=cid,
        scene=scene,
        limit=limit,
        exclude_ids=exclude_ids,
        weight_override=weight_override,
    )
    payload = _serialize_reco(ranked, state, meta, ab_bucket, ab_status)
    await set_cached_reco(cid, store_id, scene, ab_bucket, payload, fingerprint)
    payload["cache_hit"] = False
    return success(payload, request=request)


async def _load_owned_order(db, order_id: int, customer: Customer) -> Order:
    order = await db.get(Order, int(order_id))
    if not order:
        raise AppException(40404, "订单不存在")
    if int(order.customer_id or 0) != int(customer.id):
        raise AppException(40301, "无权操作该订单", status_code=403)
    return order


@router.get("/orders/{order_id}/feedback")
async def get_order_feedback(
    order_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """回显订单可评单品与已提交评价（轻量评价条渲染/置灰用）。"""
    order = await _load_owned_order(db, order_id, customer)
    return success(await FeedbackService.order_feedback_payload(db, order), request=request)


@router.post("/orders/{order_id}/feedback")
async def submit_order_feedback(
    order_id: int,
    data: FeedbackBatchIn,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """提交订单单品轻量评价：仅本人已支付订单；每单品一次，重复提交幂等。"""
    order = await _load_owned_order(db, order_id, customer)
    if (order.payment_status or "") not in EVALUABLE_PAYMENT_STATUSES:
        raise AppException(40061, "订单未支付，暂不能评价")
    try:
        result = await FeedbackService.submit_order_feedback(
            db, order, [it.model_dump() for it in data.items]
        )
    except ValueError as e:
        raise AppException(40060, str(e)) from e
    return success(result, request=request)


@router.post("/logout")
async def customer_logout(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """顾客登出：递增 token_version 使旧 token 立即失效。

    P2-3：JWT 吊销补齐。原实现仅依赖前端清除本地 token，旧 token 在 TTL 内仍可被
    重放（如设备被他人使用、Token 泄露）。现服务端递增 token_version，使任何持有
    旧 token 的请求都立即 401，配合前端清 storage 实现真正吊销。
    """
    customer.token_version = int(getattr(customer, "token_version", 0) or 0) + 1
    await db.flush()
    await AuditService.log(
        db,
        action="customer.logout",
        module="customer",
        user_id=None,
        target_type="customer",
        target_id=int(customer.id),
        detail={"channel": _customer_channel_from_request(request, default="wechat")},
        request=request,
        category=AuditService.CATEGORY_CUSTOMER,
    )
    return success({"revoked": True}, request=request)


async def _face_status(db, customer_id: int) -> dict:
    result = await db.execute(
        select(FaceProfile).where(
            FaceProfile.customer_id == customer_id,
            FaceProfile.consent_status == "AUTHORIZED",
        )
    )
    profile = result.scalar_one_or_none()
    return {
        "consent_status": profile.consent_status if profile else "NONE",
        "has_face": profile is not None,
    }


async def _store_brief(db, store_id: int | None) -> dict | None:
    if not store_id:
        return None
    store = await db.get(Store, int(store_id))
    if not store or int(store.status) != 1:
        return None
    return {
        "id": store.id,
        "store_no": store.store_no,
        "name": store.name,
        "address": store.address,
        "phone": store.phone,
    }


async def _resolve_table_record(db, token: str) -> DiningTable | None:
    from app.services.table_resolve import resolve_dining_table

    return await resolve_dining_table(db, token)


async def _table_payload(db, table: DiningTable | None) -> dict | None:
    if not table:
        return None
    payload: dict = {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
        "store_id": table.store_id,
        "qr_token": table.qr_token,
    }
    store = await _store_brief(db, table.store_id)
    if store:
        payload["store"] = store
    return payload


async def _resolve_table(db, token: str) -> dict | None:
    """Resolve a table for public entry responses without leaking ORM objects."""
    return await _table_payload(db, await _resolve_table_record(db, token))
