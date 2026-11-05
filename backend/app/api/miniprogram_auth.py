"""小程序登录/刷新/登出：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError as JWTError
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentCustomer, DbSession
from app.api.miniprogram_dine import (
    resolve_table_record as _resolve_table_record,
    table_payload as _table_payload,
)
from app.api.miniprogram_face import _face_status
from app.core.cache_keys import (
    LOGIN_CREDENTIAL_TTL_SECONDS,
    customer_refresh_rotation_key,
    login_credential_used_key,
)
from app.core.config import get_settings
from app.core.error_codes import (
    ACCOUNT_DISABLED,
    AUTH_FAILED,
    LOGIN_CREDENTIAL_REUSED,
    TOKEN_ALREADY_REFRESHED,
    TOKEN_EXPIRED,
    TOKEN_INVALID,
)
from app.core.exceptions import AppException
from app.core.redis_client import try_get_redis
from app.core.response import success
from app.core.security import create_customer_token, decode_customer_token
from app.models import Customer, CustomerAlipayAccount, CustomerWechatAccount
from app.services.audit_service import AuditService
from app.services.customer_service import CustomerService
from app.services.dining_session_service import DiningSessionService
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth

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


async def _consume_refresh_rotation(
    customer_id: int,
    token_exp: int,
    grace_hours: int,
    token_jti: str | None,
) -> bool:
    """同一旧 JWT 的 jti 只能成功刷新一次；不同设备/渠道的令牌互不影响。

    键的 TTL 覆盖旧令牌剩余有效期 + 宽限期，避免固定 24 小时后旧令牌再次可刷新。
    Redis 不可用时 fail-closed：访问令牌仍可继续使用至过期，之后重新走平台登录。
    """
    if token_exp <= 0 or not token_jti:
        return False
    try:
        r = await try_get_redis()
        if r is None:
            return False
        ttl = max(60, int(token_exp) - int(time.time()) + max(0, int(grace_hours)) * 3600)
        ok = await r.set(
            customer_refresh_rotation_key(customer_id, token_jti),
            "1",
            nx=True,
            ex=ttl,
        )
        return bool(ok)
    except Exception as exc:
        logging.getLogger(__name__).error("顾客令牌刷新旋转存储不可用，拒绝刷新: %s", exc)
        return False


def _validated_customer_channel(payload: dict) -> str:
    """从已验签且通过过期宽限校验的 JWT payload 取渠道，不重新解码过期令牌。"""
    channel = str(payload.get("channel") or "").strip().lower()
    return channel if channel in ("wechat", "alipay") else "wechat"


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

    if not await _consume_refresh_rotation(customer_id, token_exp, grace_hours, str(payload.get("jti") or "")):
        raise AppException(TOKEN_ALREADY_REFRESHED, "令牌已被刷新，请使用最新令牌重试", status_code=401)

    channel = _validated_customer_channel(payload)
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
