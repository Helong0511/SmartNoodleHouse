"""小程序堂食选桌与扫码入口：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentCustomer, DbSession
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import DiningTable, Store
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService
from app.services.dining_session_service import DiningSessionService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])


async def store_brief(db, store_id: int | None) -> dict | None:
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


async def resolve_table_record(db, token: str) -> DiningTable | None:
    from app.services.table_resolve import resolve_dining_table

    return await resolve_dining_table(db, token)


async def table_payload(db, table: DiningTable | None) -> dict | None:
    if not table:
        return None
    payload: dict = {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
        "store_id": table.store_id,
        "qr_token": table.qr_token,
    }
    store = await store_brief(db, table.store_id)
    if store:
        payload["store"] = store
    return payload


async def resolve_table(db, token: str) -> dict | None:
    """Resolve a table for public entry responses without leaking ORM objects."""
    return await table_payload(db, await resolve_table_record(db, token))

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

    store = await store_brief(db, table.store_id)
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

    store = await store_brief(db, table.store_id)
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


