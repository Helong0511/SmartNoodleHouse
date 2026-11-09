"""微信小程序服务端能力：access_token、手机号、订阅消息。"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

import httpx

from app.core.config import dev_mode_allowed, get_settings
from app.core.exceptions import AppException

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _mock_allowed() -> bool:
    """微信手机号/access_token 的 Mock 统一受三重门控保护。"""
    return dev_mode_allowed("allow_auth_mock")


_PHONE_CODE_TTL = 600
_phone_code_local: dict[str, float] = {}


def _claim_phone_code_local(digest: str) -> bool:
    """进程内兜底：Redis 不可用时防同进程并发双消费。"""
    now = time.time()
    expired = [k for k, exp in _phone_code_local.items() if exp < now]
    for k in expired:
        _phone_code_local.pop(k, None)
    if digest in _phone_code_local:
        return False
    _phone_code_local[digest] = now + _PHONE_CODE_TTL
    return True


def _release_phone_code_local(digest: str) -> None:
    _phone_code_local.pop(digest, None)


def _raise_order_ready_transport_error(exc: Exception) -> None:
    """连接建立前失败可安全重试；请求发出后响应未知时必须人工核实。"""
    import httpx

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        raise exc
    from app.services.outbox_worker import ExternalSideEffectUncertain

    raise ExternalSideEffectUncertain(
        f"WECHAT order-ready notification result unknown: {type(exc).__name__}"
    ) from exc


async def get_access_token(*, force: bool = False) -> str:
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            return "mock_access_token"
        raise AppException(50030, "微信小程序未配置，无法获取 access_token")

    now = time.time()
    if not force and _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": settings.wechat_app_id,
        "secret": settings.wechat_app_secret,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
    if data.get("errcode"):
        logger.error("微信 access_token 失败: %s", data)
        raise AppException(50032, f"获取微信 access_token 失败: {data.get('errmsg', data.get('errcode'))}")
    token = data.get("access_token") or ""
    if not token:
        raise AppException(50032, "微信未返回 access_token")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in") or 7200)
    return token


async def get_phone_by_code(code: str) -> str:
    """getPhoneNumber 动态令牌换手机号。

    P0：同一 phone_code 仅允许成功消费一次（Redis NX + 进程内兜底），
    防止并发双请求用同一 code 绑定/合并两个会员。
    """
    code = (code or "").strip()
    if not code:
        raise AppException(40054, "缺少 phone_code")

    code_digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    from app.services.webhook_replay import claim_once, release_claim

    claim_token = await claim_once("wx_phone_code", code_digest, ttl=600)
    if not claim_token:
        raise AppException(40054, "授权码已使用或正在处理，请重新授权获取手机号")
    if not _claim_phone_code_local(code_digest):
        # Redis claim 必须由本次请求的 owner token 释放，不能无条件删除其他请求的锁。
        await release_claim("wx_phone_code", code_digest, claim_token)
        raise AppException(40054, "授权码已使用或正在处理，请重新授权获取手机号")

    settings = get_settings()
    try:
        if not settings.wechat_app_id or not settings.wechat_app_secret:
            # 手机号 mock 必须在 bind_member_phone 中走未验证分支；此处只接受微信官方 code。
            raise AppException(50030, "微信小程序未配置，无法解析官方手机号授权码")

        token = await get_access_token()
        url = f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"code": code})
            data = resp.json()
        if data.get("errcode"):
            if data.get("errcode") in (40001, 42001):
                token = await get_access_token(force=True)
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}",
                        json={"code": code},
                    )
                    data = resp.json()
            if data.get("errcode"):
                logger.error("微信 getPhoneNumber 失败: %s", data)
                raise AppException(50033, f"获取手机号失败: {data.get('errmsg', data.get('errcode'))}")
        info = data.get("phone_info") or {}
        phone = (info.get("purePhoneNumber") or info.get("phoneNumber") or "").strip()
        if not phone:
            raise AppException(50033, "微信未返回手机号")
        return phone
    except Exception:
        # 换号失败允许重试同一 code（微信侧可能尚未消费）
        await release_claim("wx_phone_code", code_digest, claim_token)
        _release_phone_code_local(code_digest)
        raise


def _url_link_error_hint(errcode: int | None, errmsg: str = "") -> str:
    code = int(errcode or 0)
    msg = (errmsg or "").lower()
    if code == 85407 or "no scheme permission" in msg:
        return (
            "微信未开通 URL Link/Scheme 权限：公众平台 → 账号设置 → 基本设置 → 隐私与安全 "
            "→ 开启「明文 Scheme 拉起此小程序」，并确认已开通「URL Link」能力"
        )
    if code in (40165, 85079) or "invalid weapp pagepath" in msg or "page path" in msg:
        return "页面路径须已在正式版存在；本地开发可改用系统浏览器打开本页再点「微信点餐」"
    if code:
        return f"微信 URL Link 失败({code}): {errmsg or 'unknown'}"
    return "微信 URL Link 生成失败"


async def generate_url_link(
    *,
    path: str = "pages/index/index",
    query: str = "",
    env_version: str | None = None,
) -> tuple[str | None, str | None]:
    """生成可在微信内打开的 URL Link。返回 (link, error_hint)。"""
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    if not settings.wechat_app_id or not settings.wechat_app_secret:
        return None, "未配置 WECHAT_APP_ID / WECHAT_APP_SECRET"
    ver = (
        env_version
        or SystemSettingsService.resolved_mini_program_env_version_sync()
        or "release"
    ).strip().lower()
    if ver not in ("develop", "trial", "release"):
        ver = "release"
    page = (path or "pages/index/index").strip().lstrip("/")
    q = (query or "").strip().lstrip("?")
    try:
        token = await get_access_token()
    except AppException as e:
        logger.warning("生成 URL Link 跳过：%s", e.message)
        return None, e.message
    url = f"https://api.weixin.qq.com/wxa/generate_urllink?access_token={token}"
    payload: dict[str, Any] = {
        "path": page,
        "env_version": ver,
        "expire_type": 1,
        "expire_interval": 1,
    }
    if q:
        payload["query"] = q
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
        if data.get("errcode") in (40001, 42001):
            token = await get_access_token(force=True)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.weixin.qq.com/wxa/generate_urllink?access_token={token}",
                    json=payload,
                )
                data = resp.json()
        if data.get("errcode"):
            logger.warning("微信 URL Link 失败: %s", data)
            return None, _url_link_error_hint(data.get("errcode"), str(data.get("errmsg") or ""))
        link = (data.get("url_link") or "").strip()
        if not link:
            return None, "微信未返回 url_link"
        return link, None
    except Exception as e:
        logger.warning("微信 URL Link 异常: %s", e)
        return None, f"微信 URL Link 异常: {e}"


async def generate_wxacode(
    *,
    path: str = "pages/index/index",
    query: str = "",
    env_version: str | None = None,
    width: int = 280,
) -> tuple[bytes | None, str | None]:
    """生成微信小程序码图片（扫码直接进小程序，比 weixin:// 明文 Scheme 可靠）。"""
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    if not settings.wechat_app_id or not settings.wechat_app_secret:
        return None, "未配置 WECHAT_APP_ID / WECHAT_APP_SECRET"
    ver = (
        env_version
        or SystemSettingsService.resolved_mini_program_env_version_sync()
        or "release"
    ).strip().lower()
    if ver not in ("develop", "trial", "release"):
        ver = "release"
    page = (path or "pages/index/index").strip().lstrip("/")
    q = (query or "").strip().lstrip("?")
    # getwxacode 的 path 可带 query；总长度须 < 1024
    full_path = f"{page}?{q}" if q else page
    if len(full_path) > 1024:
        return None, "小程序码 path 过长，请缩短 token"
    try:
        token = await get_access_token()
    except AppException as e:
        return None, e.message

    async def _post(access: str) -> httpx.Response:
        url = f"https://api.weixin.qq.com/wxa/getwxacode?access_token={access}"
        payload: dict[str, Any] = {
            "path": full_path,
            "width": max(280, min(int(width or 280), 1280)),
            "env_version": ver,
            "check_path": False,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            return await client.post(url, json=payload)

    try:
        resp = await _post(token)
        ctype = (resp.headers.get("content-type") or "").lower()
        raw = resp.content
        if "application/json" in ctype or (raw[:1] == b"{"):
            data = resp.json()
            if data.get("errcode") in (40001, 42001):
                token = await get_access_token(force=True)
                resp = await _post(token)
                ctype = (resp.headers.get("content-type") or "").lower()
                raw = resp.content
                if "application/json" in ctype or (raw[:1] == b"{"):
                    data = resp.json()
                    logger.warning("微信小程序码失败: %s", data)
                    return None, _wxacode_error_hint(data.get("errcode"), str(data.get("errmsg") or ""))
            else:
                logger.warning("微信小程序码失败: %s", data)
                return None, _wxacode_error_hint(data.get("errcode"), str(data.get("errmsg") or ""))
        if not raw or len(raw) < 100:
            return None, "微信未返回小程序码图片"
        return raw, None
    except Exception as e:
        logger.warning("微信小程序码异常: %s", e)
        return None, f"微信小程序码异常: {e}"


def _wxacode_error_hint(errcode: int | None, errmsg: str = "") -> str:
    code = int(errcode or 0)
    msg = (errmsg or "").lower()
    if code in (41030, 40165, 85079) or "invalid page" in msg or "page path" in msg:
        return "页面路径无效：请确认 pages/index/index 存在，开发版请在开发者工具编译并真机预览过"
    if code == 45009:
        return "小程序码调用频率超限，请稍后重试"
    if code:
        return f"微信小程序码失败({code}): {errmsg or 'unknown'}"
    return "微信小程序码生成失败"


async def send_balance_low_subscribe(
    *,
    openid: str,
    customer_id: int,
    balance: str,
    threshold: str,
) -> bool:
    """储值余额偏低订阅消息（未配置模板则跳过）。"""
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    tmpl = SystemSettingsService.resolved_str_sync(
        "wechat_subscribe_template_balance_low",
        getattr(settings, "wechat_subscribe_template_balance_low", "") or "",
    ).strip()
    if not tmpl or not openid:
        return False
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            logger.info(
                "Mock 订阅消息 balance_low customer=%s balance=%s",
                customer_id,
                balance,
            )
            return True
        return False

    token = await get_access_token()
    url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
    payload = {
        "touser": openid,
        "template_id": tmpl,
        "page": "pages/recharge/recharge",
        "miniprogram_state": "formal",
        "lang": "zh_CN",
        "data": {
            "amount1": {"value": f"{balance}元"},
            "thing2": {"value": "储值余额偏低，欢迎到店充值"},
            "amount3": {"value": f"{threshold}元"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
        if data.get("errcode") == 0:
            return True
        logger.warning("余额提醒订阅失败 customer=%s err=%s", customer_id, data)
    except Exception as e:
        logger.warning("余额提醒订阅异常 customer=%s: %s", customer_id, e)
    return False


async def send_order_ready_subscribe(
    *,
    openid: str,
    order_no: str,
    order_id: int,
    table_label: str | None = None,
) -> bool:
    """订单 READY 时发送订阅消息（未配置模板则跳过）。"""
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    tmpl = SystemSettingsService.resolved_str_sync(
        "wechat_subscribe_template_order_ready",
        settings.wechat_subscribe_template_order_ready or "",
    ).strip()
    if not tmpl or not openid:
        return False
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        if _mock_allowed():
            import hashlib

            identity_fp = hashlib.sha256(openid.encode("utf-8")).hexdigest()[:12]
            logger.info("Mock 订阅消息 order_ready identity_fp=%s order=%s", identity_fp, order_no)
            return True
        return False

    table_text = (table_label or "堂食").strip()[:20] or "堂食"
    payload = {
        "touser": openid,
        "template_id": tmpl,
        "page": f"packageOrder/result/result?order_id={order_id}",
        "miniprogram_state": "formal",
        "lang": "zh_CN",
        "data": {
            # 请在微信公众平台选用含以下关键字的模板，或按需调整字段名
            "character_string1": {"value": (order_no or "-")[:32]},
            "thing2": {"value": "餐品已备好，请取餐"},
            "thing3": {"value": table_text},
        },
    }

    # access_token 可能在缓存有效期内被平台提前失效；对明确的凭证错误
    # 强制刷新后仅重发一次。不能对未知网络结果盲目重发。
    for token_attempt in range(2):
        token = await get_access_token(force=token_attempt > 0)
        url = f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
        except Exception as e:
            logger.warning(
                "微信出餐订阅请求异常 order=%s error_type=%s",
                order_no,
                type(e).__name__,
            )
            _raise_order_ready_transport_error(e)

        # Only an explicit provider error code is retry-classifiable. A malformed
        # response (including a JSON list or a missing/non-numeric errcode) does
        # not prove that the provider rejected the side effect; fail closed to
        # UNCERTAIN rather than letting the Outbox blindly send a duplicate.
        if not isinstance(data, dict) or "errcode" not in data:
            _raise_order_ready_transport_error(
                ValueError("WeChat subscribe response is not a valid errcode object")
            )
        raw_errcode = data.get("errcode")
        if isinstance(raw_errcode, bool) or not isinstance(raw_errcode, (int, str)):
            _raise_order_ready_transport_error(
                ValueError("WeChat subscribe response contains an invalid errcode type")
            )
        if isinstance(raw_errcode, str) and not re.fullmatch(r"-?\d+", raw_errcode.strip()):
            _raise_order_ready_transport_error(
                ValueError("WeChat subscribe response contains a non-numeric errcode")
            )
        errcode = int(raw_errcode)
        if errcode == 0:
            return True
        if errcode in {40001, 40014, 42001} and token_attempt == 0:
            logger.info(
                "微信出餐订阅 access_token 已失效，强制刷新后重试 order=%s errcode=%s",
                order_no,
                errcode,
            )
            continue
        if errcode in {-1, 45011}:
            # 平台明确返回系统繁忙/频率限制，说明本次未成功；交给 Outbox 退避重试。
            raise RuntimeError(f"WeChat subscribe temporarily unavailable: errcode={errcode}")
        logger.warning(
            "微信出餐订阅被平台拒绝 order=%s errcode=%s",
            order_no,
            errcode,
        )
        return False
    return False
