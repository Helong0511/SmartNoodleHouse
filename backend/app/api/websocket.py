import asyncio
import hmac
from urllib.parse import urlparse

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jwt.exceptions import InvalidTokenError as JWTError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models import Customer, User
from app.services.websocket_manager import WS_IDLE_TIMEOUT_SECONDS, ws_manager

router = APIRouter(tags=["WebSocket"])

PROTECTED_CHANNELS = {"kitchen", "orders", "dashboard", "cameras"}
# 角色白名单之外，再按权限放行，避免自定义角色有权限却订不上频道。
# 摄像头不再按角色放行：运营角色没有 camera:read 时不能看监控。
CHANNEL_ROLES = {
    "kitchen": frozenset(),
    "orders": frozenset(),
    "dashboard": frozenset({"STORE_ADMIN"}),
    "cameras": frozenset(),
}
CHANNEL_PERMISSION = {
    "kitchen": "order:update",
    "orders": "order:read",
    "dashboard": "system:manage",
    "cameras": "camera:read",
}


def staff_may_subscribe(role: str | None, channel: str, perms: set[str]) -> bool:
    """员工是否可订阅该实时频道。超管始终允许。"""
    if role == "SUPER_ADMIN":
        return True
    if role and role in CHANNEL_ROLES.get(channel, frozenset()):
        return True
    need = CHANNEL_PERMISSION.get(channel)
    return bool(need and need in perms)

# 长 JWT 不得出现在 query（易进访问日志）；短 opaque display token 允许
_JWT_QUERY_MIN_LEN = 80


def _looks_like_jwt(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 3 and all(parts) and len(token) >= _JWT_QUERY_MIN_LEN


def _extract_ws_token(websocket: WebSocket, query_token: str | None) -> tuple[str | None, str | None]:
    """
    返回 (token, reject_reason)。
    优先 Sec-WebSocket-Protocol；query 禁止放 JWT（叫号屏短 opaque 除外）。
    """
    proto = websocket.headers.get("sec-websocket-protocol") or ""
    header_token: str | None = None
    if proto:
        parts = [p.strip() for p in proto.split(",")]
        if len(parts) >= 2 and parts[0].lower() in ("bearer", "access_token"):
            header_token = parts[1]
        elif len(parts) == 1 and parts[0].count(".") == 2:
            header_token = parts[0]

    q = (query_token or "").strip() or None
    if q and _looks_like_jwt(q):
        return None, "禁止将 JWT 放入 WebSocket query，请使用 Sec-WebSocket-Protocol"
    if header_token:
        return header_token, None
    return q, None


async def _load_staff_user(db, token: str) -> User | None:
    try:
        payload = decode_token(token)
        if payload.get("typ") != "staff":
            return None
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    user = (
        await db.execute(
            select(User).options(selectinload(User.role)).where(User.id == user_id, User.status == 1)
        )
    ).scalar_one_or_none()
    if not user:
        return None
    if int(payload.get("ver") or 0) != int(getattr(user, "token_version", 0) or 0):
        return None
    return user


async def _load_customer(db, token: str) -> Customer | None:
    try:
        payload = decode_token(token)
        if payload.get("typ") != "customer":
            return None
        customer_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
    customer = await db.get(Customer, customer_id)
    if (
        not customer
        or customer.status != 1
        or str(getattr(customer, "account_status", "ACTIVE") or "ACTIVE").upper() != "ACTIVE"
    ):
        return None
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        return None
    return customer


async def _ws_loop(channel: str, websocket: WebSocket) -> None:
    """收消息心跳；空闲超时断开。客户端发送 ping 维持连接。"""
    while True:
        try:
            text = await asyncio.wait_for(websocket.receive_text(), timeout=WS_IDLE_TIMEOUT_SECONDS)
            ws_manager.touch(websocket)
            # P0-2：响应心跳 ping，让客户端确认连接存活
            if text:
                stripped = text.strip().lower()
                if stripped in ("ping", "heartbeat") or stripped.startswith('{"type":"ping"'):
                    try:
                        await websocket.send_text('{"type":"pong"}')
                    except Exception:
                        pass
        except WebSocketDisconnect:
            ws_manager.disconnect(channel, websocket)
            return
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=4408, reason="空闲超时")
            except Exception:
                pass
            ws_manager.disconnect(channel, websocket)
            return
        except Exception:
            ws_manager.disconnect(channel, websocket)
            return


@router.websocket("/ws/{channel}")
async def websocket_endpoint(
    websocket: WebSocket,
    channel: str,
    token: str | None = Query(default=None),
    store_id: int | None = Query(default=None),
):
    # P1-9：Origin 校验防止跨站 WS 劫持
    origin = (websocket.headers.get("origin") or "").strip()
    if origin:
        host = (websocket.headers.get("host") or "").strip()
        try:
            parsed = urlparse(origin)
            origin_host = parsed.hostname or ""
            request_host = host.split(":")[0] if host else ""
            allowed = False
            if origin_host and request_host and origin_host == request_host:
                allowed = True
            if not allowed:
                settings = get_settings()
                cors_origins = (getattr(settings, "cors_origins", "") or "").split(",")
                cors_origins = [o.strip() for o in cors_origins if o.strip() and o.strip() != "*"]
                for o in cors_origins:
                    if urlparse(o).hostname == origin_host:
                        allowed = True
                        break
                if not allowed and settings.is_production:
                    await websocket.close(code=4403, reason="Origin 不允许")
                    return
        except Exception:
            pass

    auth_token, reject = _extract_ws_token(websocket, token)
    if reject:
        await websocket.close(code=4401, reason=reject[:120])
        return

    user_store_id = None
    role = None
    is_super = False
    user_key: str | None = None
    customer_id: int | None = None

    if channel == "display":
        if not store_id or int(store_id) <= 0:
            await websocket.close(code=4400, reason="缺少 store_id")
            return
        settings = get_settings()
        try:
            from app.services.customer_display import is_customer_display_enabled

            async with AsyncSessionLocal() as db:
                enabled = await is_customer_display_enabled(db, int(store_id))
        except Exception:
            enabled = bool(settings.enable_customer_display)
        if not enabled:
            await websocket.close(code=4403, reason="本店未开通大厅叫号屏")
            return
        global_token = (settings.display_screen_token or "").strip()
        # 生产环境必须使用本门店独立令牌；不允许用一个全局 token 订阅任意门店。
        expected = ""
        try:
            from app.models import StoreSetting

            async with AsyncSessionLocal() as db:
                store_tok = await db.scalar(
                    select(StoreSetting.setting_value).where(
                        StoreSetting.store_id == int(store_id),
                        StoreSetting.setting_key == "display_screen_token",
                    )
                )
            expected = (store_tok or "").strip()
        except Exception:
            # 数据库不可用时生产必须拒绝；不能降级为全局令牌或匿名连接。
            expected = ""
        if not expected and settings.is_development:
            # 全局环境变量仅作为本地开发便利回退，不用于生产门店鉴权。
            expected = global_token
        if settings.is_production and not expected:
            await websocket.close(code=4401, reason="本门店未配置独立叫号屏令牌")
            return
        if expected:
            supplied = (auth_token or "").strip()
            if not hmac.compare_digest(supplied, expected):
                await websocket.close(code=4401, reason="无效令牌")
                return
            if _looks_like_jwt(supplied):
                await websocket.close(code=4401, reason="叫号屏须使用短 opaque token，勿用 JWT")
                return
        deny = ws_manager.can_accept(websocket, user_key=f"display:{store_id}")
        if deny:
            await websocket.close(code=4429, reason=deny)
            return
        try:
            await ws_manager.connect(
                "display",
                websocket,
                store_id=int(store_id),
                user_key=f"display:{store_id}",
            )
        except PermissionError as e:
            await websocket.close(code=4429, reason=str(e)[:120])
            return
        try:
            await _ws_loop("display", websocket)
        except WebSocketDisconnect:
            ws_manager.disconnect("display", websocket)
        return

    if channel not in PROTECTED_CHANNELS:
        # 未明确列入受保护白名单的频道一律拒绝，新增频道不会意外匿名开放。
        await websocket.close(code=4403, reason="未知或未授权频道")
        return

    if channel in PROTECTED_CHANNELS:
        if not auth_token:
            await websocket.close(code=4401, reason="未登录")
            return
        async with AsyncSessionLocal() as db:
            try:
                payload = decode_token(auth_token)
                typ = payload.get("typ")
                if typ == "customer":
                    if channel != "orders":
                        await websocket.close(code=4403, reason="无权订阅此频道")
                        return
                    customer = await _load_customer(db, auth_token)
                    if not customer:
                        await websocket.close(code=4401, reason="无效令牌")
                        return
                    user_key = f"customer:{customer.id}"
                    customer_id = int(customer.id)
                elif typ != "staff":
                    await websocket.close(code=4401, reason="无效令牌类型")
                    return
                else:
                    user = await _load_staff_user(db, auth_token)
                    if not user:
                        await websocket.close(code=4401, reason="无效令牌")
                        return
                    role = user.role.code if user.role else None
                    is_super = role == "SUPER_ADMIN"
                    from app.api.deps import get_user_permissions

                    perms = set() if is_super else await get_user_permissions(db, user.role_id)
                    if not staff_may_subscribe(role, channel, perms):
                        await websocket.close(code=4403, reason="角色无权订阅")
                        return
                    user_store_id = user.store_id
                    user_key = f"staff:{user.id}"
                    if not is_super:
                        if not user_store_id:
                            await websocket.close(code=4403, reason="账号未绑定门店")
                            return
                        store_id = int(user_store_id)
            except JWTError:
                await websocket.close(code=4401, reason="无效令牌")
                return

    deny = ws_manager.can_accept(websocket, user_key=user_key)
    if deny:
        await websocket.close(code=4429, reason=deny)
        return

    effective_store = store_id if is_super else user_store_id
    try:
        await ws_manager.connect(
            channel,
            websocket,
            store_id=effective_store,
            is_super_admin=is_super and effective_store is None,
            user_key=user_key,
            customer_id=customer_id,
        )
    except PermissionError as e:
        await websocket.close(code=4429, reason=str(e)[:120])
        return
    try:
        await _ws_loop(channel, websocket)
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
