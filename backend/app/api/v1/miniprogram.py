"""小程序登录与扫码点餐入口"""
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
from app.models import Customer, CustomerPreference, DiningTable, FaceProfile, Store
from app.services.customer_service import CustomerService, PreferenceService
from app.services.coupon_service import CouponService
from app.services.face_extractor import extract_face_embedding_with_quality
from app.services.face_service import FaceService
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth
from app.utils.upload import fetch_and_save_remote_image, save_upload

router = APIRouter(prefix="/miniprogram", tags=["小程序"])


def _issue_customer_token(customer: Customer, channel: str = "wechat") -> str:
    return create_customer_token(
        customer.id,
        channel,
        token_version=int(getattr(customer, "token_version", 0) or 0),
    )


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
    phone: str | None = None
    enable_face: bool = False


class PhoneBindRequest(BaseModel):
    phone: str | None = Field(default=None, min_length=11, max_length=11, description="手动输入手机号")
    phone_code: str | None = Field(default=None, description="微信 getPhoneNumber 返回的 code")
    alipay_phone_encrypted: str | None = Field(default=None, description="支付宝 my.getPhoneNumber 返回的 response 密文")


@router.get("/config")
async def miniprogram_config(request: Request):
    """小程序前端配置（订阅模板、隐私能力开关等，不含密钥）。"""
    settings = get_settings()
    tmpl = (settings.wechat_subscribe_template_order_ready or "").strip()
    return success(
        {
            "subscribe_templates": {
                "order_ready": tmpl or None,
            },
            # 与项目根 .env 的 ENABLE_{WX|ALIPAY}_{AVATAR|NICKNAME|PHONE_BIND} 对应
            "enable_alipay_avatar": bool(settings.enable_alipay_avatar),
            "enable_alipay_nickname": bool(settings.enable_alipay_nickname),
            "enable_alipay_phone_bind": bool(settings.enable_alipay_phone_bind),
            "enable_wx_avatar": bool(settings.enable_wx_avatar),
            "enable_wx_nickname": bool(settings.enable_wx_nickname),
            "enable_wx_phone_bind": bool(settings.enable_wx_phone_bind),
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

    return success(
        {
            "id": table.id,
            "table_no": table.table_no,
            "table_name": table.table_name,
            "store_id": table.store_id,
            "qr_token": table.qr_token,
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
                "ask_register_member": True,
                "ask_face_welcome": False,
                "face_consent_text": "",
                "member_consent_text": "注册会员后系统会智能记录您的口味爱好，支持一键复购；完成注册可领取欢迎券并享受会员等级权益。",
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
    png, err = await generate_wxacode(
        path="pages/index/index",
        query=f"token={table.qr_token}&channel=wechat",
        env_version=settings.resolved_mini_program_env_version,
        width=280,
    )
    if not png:
        raise AppException(50034, err or "生成微信小程序码失败")
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
    # 登录不接受客户端缓存写回新客；资料完善只走 /member/register
    profile_ready = bool(customer.nickname or customer.display_name)
    if profile_ready:
        if data.nickname:
            customer.nickname = data.nickname
            customer.display_name = customer.display_name or data.nickname
        if data.avatar_url:
            customer.avatar_url = data.avatar_url
    await db.flush()

    has_nickname = bool(customer.nickname or customer.display_name)
    customer_token = _issue_customer_token(customer, "wechat")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname or customer.display_name,
            "avatar_url": customer.avatar_url,
            # is_new = 库里刚创建且尚无昵称；禁止用 is_mock 冒充新客（否则开发环境每次都弹注册）
            "is_new": not has_nickname,
            "need_member_prompt": not has_nickname,
            "face_status": face_status,
            "table": table,
            "is_mock": bool(session.get("is_mock")),
        },
        request=request,
    )


@router.post("/alipay/login")
async def alipay_mini_login(data: AlipayMiniLoginRequest, request: Request, db: DbSession):
    auth = AlipayMiniAuth()
    session = await auth.auth_code2user(data.auth_code)
    customer = await CustomerService.get_or_create_by_alipay(db, session["alipay_user_id"])
    # 登录不接受客户端缓存写回新客；资料完善只走 /member/register
    profile_ready = bool(customer.nickname or customer.display_name)
    if profile_ready:
        if data.nickname:
            customer.nickname = data.nickname
            customer.display_name = customer.display_name or data.nickname
        if data.avatar_url:
            customer.avatar_url = data.avatar_url
    await db.flush()

    has_nickname = bool(customer.nickname or customer.display_name)
    customer_token = _issue_customer_token(customer, "alipay")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname or customer.display_name,
            "avatar_url": customer.avatar_url,
            "is_new": not has_nickname,
            "need_member_prompt": not has_nickname,
            "face_status": face_status,
            "table": table,
            "is_mock": bool(session.get("is_mock")),
        },
        request=request,
    )


@router.post("/member/register")
async def register_member(
    data: MemberRegisterRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    if data.nickname:
        customer.nickname = data.nickname
        customer.display_name = data.nickname
    avatar = _persistable_avatar_url(data.avatar_url)
    if avatar:
        customer.avatar_url = avatar
    if data.phone:
        customer = await CustomerService.try_link_by_phone(db, customer, data.phone)

    await CustomerService.ensure_default_member_level(db, customer)
    issued = await CouponService.issue_welcome_coupon(db, customer.id)
    await db.flush()
    profile = await CustomerService.build_member_profile(db, customer)
    welcome = await CouponService.welcome_coupon_payload(db, issued)
    payload = {
        "customer_id": customer.id,
        "customer_no": customer.customer_no,
        "nickname": customer.nickname,
        "avatar_url": customer.avatar_url,
        "phone": customer.phone,
        "face_status": "NONE",
        "member_registered": True,
        **profile,
        "need_face_consent": bool(data.enable_face),
        # 若因手机号合并到老顾客，前端需刷新 token
        "customer_token": _issue_customer_token(customer, "merged"),
        "welcome_coupon_issued": welcome is not None,
        "welcome_coupon": welcome,
        "welcome_coupon_name": CouponService.welcome_template_name(),
    }
    if data.enable_face:
        return success(payload, message="会员信息已保存，请完成摄像头人脸采集授权", request=request)
    msg = "注册成功"
    if welcome:
        msg = f"注册成功，已赠送「{welcome.get('name') or CouponService.welcome_template_name()}」"
    return success(payload, message=msg, request=request)


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
    customer.avatar_url = url
    await db.flush()
    return success(
        {
            "avatar_url": customer.avatar_url,
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
    customer.avatar_url = url
    await db.flush()
    return success(
        {
            "avatar_url": customer.avatar_url,
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

    channel = "wechat"
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            channel = decode_customer_token(auth_header.split(" ", 1)[1]).get("channel") or "wechat"
        except Exception:
            channel = "wechat"

    raw_phone = (data.phone or "").strip()
    if data.phone_code:
        if channel == "alipay":
            raise AppException(40054, "支付宝端请使用 alipay_phone_encrypted 字段")
        raw_phone = await get_phone_by_code(data.phone_code)
    elif data.alipay_phone_encrypted:
        raw_phone = decrypt_phone_response(data.alipay_phone_encrypted)
    if not raw_phone:
        raise AppException(40054, "请提供手机号或授权获取手机号")
    if not re.fullmatch(r"1\d{10}", raw_phone):
        raise AppException(40053, "手机号格式不正确")
    customer = await CustomerService.try_link_by_phone(db, customer, raw_phone)
    issued = await CouponService.issue_welcome_coupon(db, customer.id)
    await db.flush()
    profile = await CustomerService.build_member_profile(db, customer)
    welcome = await CouponService.welcome_coupon_payload(db, issued)
    return success(
        {
            "phone": customer.phone,
            "customer_id": customer.id,
            "customer_token": _issue_customer_token(customer, "merged"),
            "welcome_coupon_issued": welcome is not None,
            "welcome_coupon": welcome,
            "welcome_coupon_name": CouponService.welcome_template_name(),
            **profile,
        },
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
    """顾客主动授权：摄像头实拍后上传，服务端质量+活体校验并提取特征，禁止客户端伪造向量"""
    settings = get_settings()
    raw = await image.read()
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(raw) > max_size:
        raise AppException(40030, f"图片超过 {settings.max_upload_size_mb}MB")

    try:
        feature, quality = extract_face_embedding_with_quality(raw)
    except ValueError as e:
        raise AppException(40031, str(e)) from e

    profile = await FaceService.register_face(db, customer.id, feature)
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
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
    """小程序摄像头 takePhoto 后的 base64 授权（禁止相册选图）"""
    body = await request.json()
    b64 = body.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        raise AppException(40032, "缺少 image_base64")
    # 占位/过短串直接拒绝（如 H5 假数据）
    if len(b64) < 800:
        raise AppException(40033, "人脸图像无效，请使用小程序摄像头实时采集")
    try:
        raw = base64.b64decode(b64)
        if len(raw) < 1024:
            raise AppException(40034, "人脸图像过小，请重新正对摄像头采集")
        feature, quality = extract_face_embedding_with_quality(raw)
    except AppException:
        raise
    except ValueError as e:
        raise AppException(40031, str(e)) from e
    except Exception as e:
        raise AppException(40035, f"人脸采集失败: {e}") from e

    profile = await FaceService.register_face(db, customer.id, feature)
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
            },
        },
        request=request,
    )


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    await FaceService.revoke_face(db, customer.id)
    return success({"revoked": True}, request=request)


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
    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "nickname": customer.nickname or customer.display_name,
            "avatar_url": customer.avatar_url,
            "phone": customer.phone,
            **profile,
            "face_status": await _face_status(db, customer.id),
        },
        request=request,
    )


@router.get("/customers/me/preferences")
async def get_my_preferences(request: Request, db: DbSession, customer: CurrentCustomer):
    """当前会员口味偏好（小程序专用，以令牌身份为准，避免本地 customer_id 过期）。"""
    prefs = await PreferenceService.get_recommendations(db, customer.id)
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
