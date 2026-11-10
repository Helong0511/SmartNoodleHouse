"""小程序会员注册/资料/手机号：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentCustomer, DbSession
from app.api.miniprogram import (
    _customer_channel_from_request,
    _issue_customer_token,
    _persistable_avatar_url,
)
from app.api.miniprogram_face import _face_status
from app.core.config import dev_mode_allowed, get_settings
from app.core.exceptions import AppException
from app.core.response import now_local, serialize_dt, success
from app.models import Customer
from app.services.audit_service import AuditService
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService
from app.services.private_media import sign_private_avatar_url
from app.utils.upload import fetch_and_save_remote_image, read_upload_limited, save_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

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
    birthday: str | None = Field(default=None, description="生日 YYYY-MM-DD；传空串清除")
    birthday_calendar: str | None = Field(
        default=None, description="SOLAR=公历 / LUNAR=农历；生日清空时忽略"
    )
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
    store_id = int(getattr(customer, "registered_store_id", 0) or 0) or None
    issued = None
    if newly_activated:
        issued = await CouponService.issue_welcome_coupon(db, customer.id, store_id=store_id)
    await db.flush()
    profile = await CustomerService.build_member_profile(db, customer)
    welcome = await CouponService.welcome_coupon_payload(db, issued)
    disp = await CustomerService.resolve_channel_profile(db, customer, channel)
    welcome_name = await CouponService.welcome_template_name(db, store_id=store_id)
    payload = {
        "customer_id": customer.id,
        "customer_no": customer.customer_no,
        "nickname": disp["nickname"] or nickname,
        "avatar_url": sign_private_avatar_url(disp["avatar_url"] or avatar, customer.id),
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
        "welcome_coupon_name": welcome_name,
    }
    if enable_face:
        return success(payload, message="会员已开通，请完成摄像头人脸采集授权", request=request)
    if newly_activated and welcome:
        msg = f"已开启智能会员，赠送「{welcome.get('name') or welcome_name}」"
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


@router.api_route("/member/profile", methods=["PATCH", "PUT"])
async def patch_member_profile(
    data: MemberProfilePatchRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """修改昵称/头像/生日，不触发会员激活与欢迎券。

    同时接受 PATCH/PUT：微信/支付宝小程序对 PATCH 支持不一致，客户端优先 PUT。
    """
    channel = _customer_channel_from_request(request, "wechat", body_channel=data.channel)
    avatar = _persistable_avatar_url(data.avatar_url)
    from app.utils.birthday import (
        VALID_CALENDARS,
        birthday_for_picker,
        format_birthday_display,
        normalize_calendar,
    )

    # 生日：YYYY-MM-DD；传空串清除；不得晚于今天。
    # 历法=农历时，控件数字即农历年月日，落库前换算为公历锚点。
    # 同请求既改历法又改日期时，先定历法再解释日期。
    # 小程序确认后 birthday_locked=1，不可再改历法/日期（后台可改）。
    birthday_locked = int(getattr(customer, "birthday_locked", 0) or 0) == 1
    touching_birthday = "birthday" in data.model_fields_set
    touching_calendar = (
        "birthday_calendar" in data.model_fields_set and data.birthday_calendar is not None
    )
    if birthday_locked and (touching_birthday or touching_calendar):
        raise AppException(40033, "生日已确认，不可自行修改，请联系门店")
    if touching_calendar:
        from app.utils.birthday import convert_birthday_calendar

        raw_cal = str(data.birthday_calendar or "").strip().upper()
        if raw_cal and raw_cal not in VALID_CALENDARS:
            raise AppException(40032, "生日历法须为公历或农历")
        if raw_cal:
            old_cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
            new_cal = normalize_calendar(raw_cal)
            # 仅改历法（未同时改日期）：同日互转，锚点公历日不变
            if (
                not touching_birthday
                and customer.birthday is not None
                and old_cal != new_cal
            ):
                converted = convert_birthday_calendar(customer.birthday, old_cal, new_cal)
                if converted is not None:
                    customer.birthday = converted
            customer.birthday_calendar = new_cal
    if touching_birthday:
        raw_birthday = (data.birthday or "").strip()
        if not raw_birthday:
            customer.birthday = None
            customer.birthday_calendar = "SOLAR"
            customer.birthday_locked = 0
        else:
            from app.utils.birthday import resolve_birthday_input

            cal_for_input = normalize_calendar(getattr(customer, "birthday_calendar", None))
            try:
                parsed_birthday = resolve_birthday_input(raw_birthday, cal_for_input)
            except ValueError:
                raise AppException(
                    40030,
                    "生日格式不正确" if cal_for_input != "LUNAR" else "农历日期无效，请重选",
                )
            if parsed_birthday > now_local().date():
                raise AppException(40031, "生日不能晚于今天")
            customer.birthday = parsed_birthday
            customer.birthday_locked = 1
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
    # 昵称归渠道/小程序；展示名仅在空或仍等于旧昵称时跟随，不覆盖员工定制展示名
    if data.nickname is not None:
        CustomerService.apply_miniprogram_nickname(customer, data.nickname)
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
    cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
    store_id = getattr(customer, "registered_store_id", None) or getattr(
        customer, "first_seen_store_id", None
    )
    # 权益说明/发券失败不得回滚已保存的生日
    try:
        benefit = await CustomerService.birthday_benefit_payload(
            db, int(store_id) if store_id else None, customer, try_issue_coupon=True
        )
    except Exception:
        logger.exception("生日权益组装失败 customer_id=%s", customer.id)
        benefit = {
            "enabled": False,
            "text": "",
            "is_birthday_today": False,
            "birthday_display": format_birthday_display(customer.birthday, cal),
            "birthday_calendar": cal,
        }
    return success(
        {
            "customer_id": customer.id,
            "nickname": disp["nickname"],
            "avatar_url": sign_private_avatar_url(disp["avatar_url"], customer.id),
            "phone": customer.phone,
            "birthday": serialize_dt(customer.birthday),
            "birthday_picker": birthday_for_picker(customer.birthday, cal),
            "birthday_calendar": cal,
            "birthday_display": format_birthday_display(customer.birthday, cal),
            "birthday_locked": int(getattr(customer, "birthday_locked", 0) or 0) == 1,
            "birthday_benefit": benefit,
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
            "avatar_url": sign_private_avatar_url(url, customer.id),
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
            "avatar_url": sign_private_avatar_url(url, customer.id),
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
            "welcome_coupon_name": await CouponService.welcome_template_name(
                db, store_id=int(getattr(customer, "registered_store_id", 0) or 0) or None
            ),
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
        if not dev_mode_allowed("allow_auth_mock"):
            raise AppException(40054, "mock 授权凭据仅限显式开启 APP_DEBUG 与 ALLOW_AUTH_MOCK 的开发环境使用")
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
            "welcome_coupon_name": await CouponService.welcome_template_name(
                db, store_id=int(getattr(customer, "registered_store_id", 0) or 0) or None
            ),
            **profile,
        },
        message=(
            "账号已安全合并，手机号验证成功"
            if merged
            else "手机号验证成功"
        ),
        request=request,
    )
