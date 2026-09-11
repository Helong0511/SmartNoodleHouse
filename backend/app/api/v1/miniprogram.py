"""小程序登录与扫码点餐入口"""
import asyncio
import base64
import re

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, File, Request, UploadFile

from app.api.deps import CurrentCustomer, DbSession
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
    Store,
)
from app.services.customer_service import CustomerService, PreferenceService
from app.services.coupon_service import CouponService
from app.services.dining_session_service import DiningSessionService
from app.services.face_extractor import extract_face_embedding_with_quality
from app.services.face_service import FaceService
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth
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
    nickname: str | None = None
    avatar_url: str | None = None
    phone: str | None = Field(default=None, pattern=r"^1\d{10}$")
    enable_face: bool = False
    channel: str | None = None
    table_token: str | None = None


class MemberActivateRequest(BaseModel):
    """主动开通智能会员（GUEST → MEMBER，幂等）。"""
    nickname: str | None = None
    avatar_url: str | None = None
    phone: str | None = Field(default=None, pattern=r"^1\d{10}$")
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
        description="手动输入手机号（仅保存为未验证，不触发账号合并）",
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
    """按 table_id 换取该桌 qr_token；须校验门店，防止跨店绑桌。"""
    from app.core.config import get_settings
    from app.core.exceptions import AppException

    body = await request.json()
    table_id = int(body.get("table_id") or 0)
    req_store = body.get("store_id")
    table = await db.get(DiningTable, table_id)
    if not table or table.status != 1:
        return success(None, message="桌台不存在或已禁用", request=request)

    settings = get_settings()
    # 生产必须带 store_id，且与桌台一致；开发可省略但传了仍校验
    if req_store is None and not settings.is_development:
        raise AppException(40050, "请先扫码进入门店后再选桌")
    if req_store is not None and int(req_store) != int(table.store_id):
        raise AppException(40052, "桌台不属于当前门店")

    session = await DiningSessionService.start_or_refresh(
        db,
        customer_id=customer.id,
        table=table,
        table_token=table.qr_token,
    )
    await CustomerService.remember_first_seen(db, customer, table.qr_token, table=table)

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

    table = await _resolve_table(db, data.table_token) if data.table_token else None
    await CustomerService.remember_first_seen(db, customer, data.table_token, table=table)
    dining_session = None
    if table:
        dining_session = await DiningSessionService.start_or_refresh(
            db,
            customer_id=customer.id,
            table=table,
            table_token=data.table_token or table.qr_token,
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

    table = await _resolve_table(db, data.table_token) if data.table_token else None
    await CustomerService.remember_first_seen(db, customer, data.table_token, table=table)
    dining_session = None
    if table:
        dining_session = await DiningSessionService.start_or_refresh(
            db,
            customer_id=customer.id,
            table=table,
            table_token=data.table_token or table.qr_token,
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
    if data.phone:
        customer = await CustomerService.save_unverified_phone(db, customer, data.phone)
    customer, newly = await CustomerService.activate_member(
        db,
        customer,
        channel=channel,
        nickname=data.nickname,
        avatar_url=avatar,
        table_token=data.table_token,
        mark_channel_completed=True,
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
    if data.phone:
        customer = await CustomerService.save_unverified_phone(db, customer, data.phone)
    customer, newly = await CustomerService.activate_member(
        db,
        customer,
        channel=channel,
        nickname=data.nickname,
        avatar_url=avatar,
        table_token=data.table_token,
        mark_channel_completed=True,
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
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    profile = await CustomerService.build_member_profile(db, customer)
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
    await CustomerService.save_channel_profile(db, customer.id, channel, avatar_url=url)
    CustomerService.fill_customer_display_if_empty(customer, avatar_url=url)
    CustomerService.refresh_profile_status(customer)
    await db.flush()
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
    await CustomerService.save_channel_profile(db, customer.id, channel, avatar_url=url)
    CustomerService.fill_customer_display_if_empty(customer, avatar_url=url)
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    return success(
        {
            "avatar_url": url,
            "customer_id": customer.id,
        },
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

    supplied = sum(
        bool(value)
        for value in (data.phone, data.phone_code, data.alipay_phone_encrypted)
    )
    if supplied != 1:
        raise AppException(40054, "手机号、微信 phone_code、支付宝授权密文必须且只能提供一种")

    raw_phone = (data.phone or "").strip()
    verified_source = None
    if data.phone_code:
        if channel == "alipay":
            raise AppException(40054, "支付宝端请使用 alipay_phone_encrypted 字段")
        raw_phone = await get_phone_by_code(data.phone_code)
        verified_source = "WECHAT"
    elif data.alipay_phone_encrypted:
        if channel != "alipay":
            raise AppException(40054, "微信端不能提交支付宝手机号授权数据")
        raw_phone = decrypt_phone_response(data.alipay_phone_encrypted)
        verified_source = "ALIPAY"
    if not raw_phone:
        raise AppException(40054, "请提供手机号或授权获取手机号")
    if not re.fullmatch(r"1\d{10}", raw_phone):
        raise AppException(40053, "手机号格式不正确")
    merged = False
    if verified_source:
        customer, merged = await CustomerService.bind_verified_phone(
            db,
            customer,
            raw_phone,
            verification_source=verified_source,
            confirm_merge=data.confirm_merge,
            request=request,
        )
    else:
        customer = await CustomerService.save_unverified_phone(db, customer, raw_phone)
    CustomerService.refresh_profile_status(customer)
    await db.flush()
    # 绑手机不发欢迎券；欢迎券仅在主动开通会员时发放
    profile = await CustomerService.build_member_profile(db, customer)
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
            if verified_source
            else "手机号已保存，完成官方授权后才会验证或合并账号"
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
async def list_my_coupons(request: Request, db: DbSession, customer: CurrentCustomer):
    """我的可用优惠券（满减/折扣，不含代金券）。"""
    items = await CouponService.list_available(db, customer.id)
    return success({"items": items}, request=request)


@router.get("/vouchers/mine")
async def list_my_vouchers(request: Request, db: DbSession, customer: CurrentCustomer):
    """我的可用代金券。"""
    items = await CouponService.list_available_vouchers(db, customer.id)
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


async def _resolve_table(db, token: str) -> dict | None:
    from app.services.table_resolve import resolve_dining_table

    table = await resolve_dining_table(db, token)
    if not table:
        return None
    payload = {
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
