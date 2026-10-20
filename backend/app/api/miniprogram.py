"""小程序登录与扫码点餐入口"""
import asyncio
import base64
import logging
import re
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

from jose import ExpiredSignatureError, JWTError
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, File, Request, UploadFile

from app.api.deps import CurrentCustomer, DbSession, OptionalCustomer
from app.core.cache_keys import (
    LOGIN_CREDENTIAL_TTL_SECONDS,
    cart_fingerprint,
    customer_refresh_rotation_key,
    login_credential_used_key,
)
from app.core.error_codes import (
    ACCOUNT_DISABLED,
    AUTH_FAILED,
    LOGIN_CREDENTIAL_REUSED,
    TOKEN_ALREADY_REFRESHED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
)
from app.core.security import create_customer_token, decode_customer_token
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.redis_client import try_get_redis
from app.core.response import money, now_local, success, serialize_dt
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    CouponTemplate,
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
from app.services.reco_cache import (
    get_cached_reco,
    release_reco_lock,
    set_cached_reco,
    try_acquire_reco_lock,
    wait_for_reco,
)
from app.services.recommendation_service import RecommendationService
from app.utils.upload import fetch_and_save_remote_image, read_upload_limited, save_upload

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

# 人脸 / 菜单拆出后的兼容再导出（测试 patch / 旧导入）
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


async def _consume_login_credential(channel: str, credential: str) -> None:
    """P2-7 防重放：登录 code/auth_code 一次性消费（5 分钟窗口）。

    - 重复使用同一凭证直接拒绝（渠道侧 code 本身也是一次性，此处提前拦截并给统一提示）；
    - Redis 不可用 fail-open，不阻断登录；
    - mock: 凭证仅存在于开发环境，跳过以便本地反复调试。
    """
    cred = (credential or "").strip()
    if not cred or cred.startswith("mock:"):
        return
    r = await try_get_redis()
    if r is None:
        return
    try:
        ok = await r.set(
            login_credential_used_key(channel, cred),
            "1",
            nx=True,
            ex=LOGIN_CREDENTIAL_TTL_SECONDS,
        )
    except Exception:
        return
    if not ok:
        raise AppException(LOGIN_CREDENTIAL_REUSED, "登录凭证已使用，请重新获取", status_code=401)


async def _consume_refresh_rotation(customer_id: int, token_exp: int, grace_hours: int) -> bool:
    """P2-6 刷新旋转保护：同一旧令牌只能成功刷新一次。

    Redis 记录该顾客已成功消费的最大旧令牌 exp（Lua 原子比较，exp 单调递增才放行）。
    Redis 不可用时 fail-open（token_version 撤销与宽限窗口校验仍然生效）。
    """
    if token_exp <= 0:
        return True
    r = await try_get_redis()
    if r is None:
        return True
    ttl = max(60, int(grace_hours) * 3600)
    script = """
    local cur = redis.call('GET', KEYS[1])
    local exp = tonumber(ARGV[1])
    if cur and tonumber(cur) >= exp then
        return 0
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    return 1
    """
    try:
        result = await r.eval(script, 1, customer_refresh_rotation_key(customer_id), int(token_exp), ttl)
        return int(result) == 1
    except Exception:
        return True


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


async def _finalize_channel_login(
    db,
    *,
    customer: Customer,
    channel: str,
    account,
    nickname: str | None,
    avatar_url: str | None,
    table_token: str | None,
    is_mock: bool,
) -> dict:
    """P2-16：微信/支付宝登录共用收尾（资料回写 → 桌台/会话 → 登录 payload）。"""
    # 登录不接受客户端缓存写回新客；资料完善走 /member/profile 或 /member/activate
    profile_ready = bool(account) and int(getattr(account, "profile_completed", 0) or 0) == 1
    if profile_ready:
        await CustomerService.save_channel_profile(
            db,
            customer.id,
            channel,
            nickname=nickname,
            avatar_url=_persistable_avatar_url(avatar_url),
        )
        CustomerService.fill_customer_display_if_empty(
            customer,
            nickname=nickname,
            avatar_url=_persistable_avatar_url(avatar_url),
        )
        CustomerService.refresh_profile_status(customer)
        await db.flush()

    table_record = await _resolve_table_record(db, table_token) if table_token else None
    table = await _table_payload(db, table_record)
    await CustomerService.remember_first_seen(db, customer, table_token, table=table_record)
    dining_session = None
    if table_record:
        dining_session = await DiningSessionService.start_or_refresh(
            db,
            customer_id=customer.id,
            table=table_record,
            table_token=table_token or table_record.qr_token,
        )
        try:
            from app.services.shared_cart_service import SharedCartService

            await SharedCartService.join_party(db, customer=customer, table=table_record)
        except Exception:
            pass
    return await _channel_login_payload(
        db,
        customer,
        channel,
        is_mock=is_mock,
        table=table,
        dining_session=dining_session,
    )


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


class BindDineTableRequest(BaseModel):
    table_id: int = Field(..., gt=0, description="目标桌台 ID")
    qr_token: str | None = Field(default=None, description="扫码获得的桌台凭证")
    token: str | None = Field(default=None, description="qr_token 别名")
    store_id: int | None = Field(default=None, description="开发环境无凭证时的门店校验")


@router.get("/config")
async def miniprogram_config(
    request: Request,
    db: DbSession,
    store_id: int | None = None,
):
    """小程序前端配置（订阅模板、隐私能力开关等，不含密钥）。

    可传 store_id：门店可覆盖的开关走 effective_bool；主体级隐私开关仍为全局。
    """
    from app.services.system_settings_service import SystemSettingsService

    settings = get_settings()
    await SystemSettingsService.ensure_seeded(db)
    sid = int(store_id) if store_id else None
    tmpl = (
        await SystemSettingsService.get_value(
            db,
            "wechat_subscribe_template_order_ready",
            settings.wechat_subscribe_template_order_ready or "",
        )
    ).strip()
    tmpl_balance_low = (
        await SystemSettingsService.get_value(
            db,
            "wechat_subscribe_template_balance_low",
            getattr(settings, "wechat_subscribe_template_balance_low", "") or "",
        )
    ).strip()
    alipay_tmpl_ready = (
        await SystemSettingsService.get_value(db, "alipay_subscribe_template_order_ready", "")
    ).strip()
    alipay_tmpl_balance = (
        await SystemSettingsService.get_value(db, "alipay_subscribe_template_balance_low", "")
    ).strip()

    async def _flag(key: str, fallback: bool) -> bool:
        return await SystemSettingsService.effective_bool(
            db, key, fallback, store_id=sid
        )

    # 快捷备注：逗号分隔；支持门店覆盖
    _DEFAULT_REMARKS = ["不要香菜", "少油少盐", "不要辣", "微辣", "多加醋", "加份米饭"]
    remark_raw = (
        await SystemSettingsService.effective_setting(
            db,
            "quick_remark_options",
            store_id=sid,
            fallback=",".join(_DEFAULT_REMARKS),
        )
    ).strip()
    quick_remark_options = [s.strip() for s in remark_raw.split(",") if s.strip()] or list(_DEFAULT_REMARKS)
    pay_success_coupon_name = (
        await SystemSettingsService.effective_setting(
            db, "pay_success_coupon_name", store_id=sid, fallback=""
        )
    ).strip()
    queue_enabled = await SystemSettingsService.effective_bool(
        db, "queue_enabled", False, store_id=sid
    )
    invoice_enabled = await SystemSettingsService.effective_bool(
        db, "invoice_enabled", False, store_id=sid
    )
    shared_cart_enabled = await SystemSettingsService.effective_bool(
        db, "shared_cart_enabled", False, store_id=sid
    )

    config_version = await SystemSettingsService.public_config_version()

    return success(
        {
            "store_id": sid,
            # 全局/门店业务写路径 bump；小程序用此字段在 TTL 内静默探测热更
            "config_version": int(config_version or 0),
            "subscribe_templates": {
                # 微信端只用 order_ready / balance_low；支付宝只用 alipay_*，禁止跨端混用
                "order_ready": tmpl or None,
                "balance_low": tmpl_balance_low or None,
                "alipay_order_ready": alipay_tmpl_ready or None,
                "alipay_balance_low": alipay_tmpl_balance or None,
            },
            # 优先 system_settings / 门店覆盖，种子默认值来自 .env ENABLE_*
            "enable_alipay_avatar": await _flag("enable_alipay_avatar", settings.enable_alipay_avatar),
            "enable_alipay_nickname": await _flag("enable_alipay_nickname", settings.enable_alipay_nickname),
            "enable_alipay_phone_bind": await _flag(
                "enable_alipay_phone_bind", settings.enable_alipay_phone_bind
            ),
            "enable_wx_avatar": await _flag("enable_wx_avatar", settings.enable_wx_avatar),
            "enable_wx_nickname": await _flag("enable_wx_nickname", settings.enable_wx_nickname),
            "enable_wx_phone_bind": await _flag("enable_wx_phone_bind", settings.enable_wx_phone_bind),
            "enable_face_welcome": await _flag(
                "enable_face_welcome", settings.enable_face_welcome
            ),
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
            "enable_social_benefit": await _flag("enable_social_benefit", True),
            "enable_marketing_campaigns": await _flag("enable_marketing_campaigns", True),
            "enable_customer_display": await _flag(
                "enable_customer_display", settings.enable_customer_display
            ),
            "quick_remark_options": quick_remark_options,
            "pay_success_coupon_name": pay_success_coupon_name,
            "queue_enabled": queue_enabled,
            "invoice_enabled": invoice_enabled,
            "shared_cart_enabled": shared_cart_enabled,
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
    body: BindDineTableRequest,
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

    table_id = int(body.table_id)
    qr_token = (body.qr_token or body.token or "").strip()
    req_store = body.store_id
    table = await db.get(DiningTable, table_id)
    if not table or table.status != 1:
        raise AppException(40404, "桌台不存在或已禁用")

    settings = get_settings()
    # 生产必须带 qr_token 凭证；开发可省略但传了仍校验
    if not qr_token and not settings.is_development:
        raise AppException(40050, "请先扫码进入门店后再选桌")
    if qr_token:
        # 校验 qr_token 凭证：必须能解析到桌台，且与目标桌台为同一桌（禁止同店互绑盗永久码）
        cred_table = await resolve_dining_table(db, qr_token)
        if not cred_table:
            raise AppException(40050, "扫码凭证无效，请重新扫码")
        if int(cred_table.id) != int(table.id):
            raise AppException(40052, "扫码桌台与所选桌台不一致，请重新扫码")
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
    from app.services.shared_cart_service import SharedCartService

    shared_cart = await SharedCartService.join_party(db, customer=customer, table=table)

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
            # 不回传永久 qr_token，避免同店扫码后枚举 table_id 盗取全店桌台凭证
            "dining_session": DiningSessionService._session_payload(session),
            "shared_cart": shared_cart,
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
        raise AppException(40404, "无效桌台二维码")

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
                "face_consent_text": "开启人脸迎宾后到店可自动欢迎。注册时需完成动作挑战与摄像头实拍（防重放 token），图像另做清晰度/亮度粗检；可随时关闭。",
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
        raise AppException(40404, "无效桌台二维码")

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


@router.post("/auth/refresh")
async def refresh_customer_token(request: Request, db: DbSession):
    """P2-6：顾客令牌滑动续期（POST /miniprogram/auth/refresh，须携带旧令牌）。

    - 未过期令牌任意时刻可换新；过期后 customer_refresh_grace_hours 宽限内仍可换新；
    - 旋转保护：同一旧令牌只能成功刷新一次（防止旧令牌在宽限期内被反复重放）；
    - token_version 撤销语义保持：版本不匹配即拒绝。
    """
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise AppException(AUTH_FAILED, "请先登录", status_code=401)
    token = auth_header.split(" ", 1)[1]
    expired = False
    # 0 是合法值（关闭宽限刷新），不能用 or 兜底；仅 None/缺省时取默认 24
    raw_grace = getattr(get_settings(), "customer_refresh_grace_hours", None)
    grace_hours = 24 if raw_grace is None else int(raw_grace)
    try:
        payload = decode_customer_token(token)
    except ExpiredSignatureError:
        # 宽限期刷新：leeway 放宽 exp 判定到宽限窗口；窗口外仍过期即拒绝
        try:
            payload = decode_customer_token(token, leeway_seconds=grace_hours * 3600)
        except JWTError:
            raise AppException(TOKEN_EXPIRED, "登录已过期，请重新登录", status_code=401)
        expired = True
    except JWTError:
        raise AppException(TOKEN_INVALID, "无效顾客令牌", status_code=401)

    token_exp = int(payload.get("exp") or 0)

    try:
        customer_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise AppException(TOKEN_INVALID, "无效顾客令牌", status_code=401)
    customer = await db.get(Customer, customer_id)
    if (
        not customer
        or customer.status != 1
        or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE"
    ):
        raise AppException(ACCOUNT_DISABLED, "顾客不存在或已禁用", status_code=401)
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        raise AppException(TOKEN_INVALID, "令牌已失效，请重新登录", status_code=401)

    if not await _consume_refresh_rotation(customer_id, token_exp, grace_hours):
        raise AppException(TOKEN_ALREADY_REFRESHED, "令牌已被刷新，请使用最新令牌重试", status_code=401)

    channel = _customer_channel_from_request(request, "wechat")
    new_token = _issue_customer_token(customer, channel)
    return success(
        {
            "customer_token": new_token,
            "customer_id": customer.id,
            "channel": channel,
        },
        request=request,
    )


@router.post("/wechat/login")
async def wechat_mini_login(data: WechatMiniLoginRequest, request: Request, db: DbSession):
    await _consume_login_credential("wechat", data.code)
    auth = WechatMiniAuth()
    session = await auth.code2session(data.code)
    customer = await CustomerService.get_or_create_by_wechat(db, session["openid"], session.get("unionid"))
    acc = (
        await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.openid == session["openid"]))
    ).scalar_one_or_none()
    payload = await _finalize_channel_login(
        db,
        customer=customer,
        channel="wechat",
        account=acc,
        nickname=data.nickname,
        avatar_url=data.avatar_url,
        table_token=data.table_token,
        is_mock=bool(session.get("is_mock")),
    )
    return success(payload, request=request)


@router.post("/alipay/login")
async def alipay_mini_login(data: AlipayMiniLoginRequest, request: Request, db: DbSession):
    await _consume_login_credential("alipay", data.auth_code)
    auth = AlipayMiniAuth()
    session = await auth.auth_code2user(data.auth_code)
    customer = await CustomerService.get_or_create_by_alipay(db, session["alipay_user_id"])
    acc = (
        await db.execute(
            select(CustomerAlipayAccount).where(CustomerAlipayAccount.alipay_user_id == session["alipay_user_id"])
        )
    ).scalar_one_or_none()
    payload = await _finalize_channel_login(
        db,
        customer=customer,
        channel="alipay",
        account=acc,
        nickname=data.nickname,
        avatar_url=data.avatar_url,
        table_token=data.table_token,
        is_mock=bool(session.get("is_mock")),
    )
    return success(payload, request=request)


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
        "logo": getattr(store, "logo", None),
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
