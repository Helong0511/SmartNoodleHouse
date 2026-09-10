import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models import Customer, User
from app.services.websocket_manager import WS_IDLE_TIMEOUT_SECONDS, ws_manager

router = APIRouter(tags=["WebSocket"])

PROTECTED_CHANNELS = {"kitchen", "orders", "dashboard", "cameras"}
CHANNEL_PERMS = {
    "kitchen": {"SUPER_ADMIN", "STORE_ADMIN", "KITCHEN"},
    "orders": {"SUPER_ADMIN", "STORE_ADMIN", "CASHIER", "OPERATOR", "KITCHEN"},
    "dashboard": {"SUPER_ADMIN", "STORE_ADMIN"},
    "cameras": {"SUPER_ADMIN", "STORE_ADMIN", "OPERATOR"},
}

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
    if not customer or customer.status != 1:
        return None
    if int(payload.get("ver") or 0) != int(getattr(customer, "token_version", 0) or 0):
        return None
    return customer


async def _ws_loop(channel: str, websocket: WebSocket) -> None:
    """收消息心跳；空闲超时断开。"""
    while True:
        try:
            await asyncio.wait_for(websocket.receive_text(), timeout=WS_IDLE_TIMEOUT_SECONDS)
            ws_manager.touch(websocket)
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=4408, reason="空闲超时")
            except Exception:
                pass
            ws_manager.disconnect(channel, websocket)
            return


@router.websocket("/ws/{channel}")
async def websocket_endpoint(
    websocket: WebSocket,
    channel: str,
    token: str | None = Query(default=None),
    store_id: int | None = Query(default=None),
):
    auth_token, reject = _extract_ws_token(websocket, token)
    if reject:
        await websocket.close(code=4401, reason=reject[:120])
        return

    user_store_id = None
    role = None
    is_super = False
    user_key: str | None = None

    if channel == "display":
        if not store_id or int(store_id) <= 0:
            await websocket.close(code=4400, reason="缺少 store_id")
            return
        settings = get_settings()
        expected = (settings.display_screen_token or "").strip()
        # 生产强制（与 startup_checks 一致）；开发可空
        if settings.is_production and not expected:
            await websocket.close(code=4401, reason="未配置 DISPLAY_SCREEN_TOKEN")
            return
        if expected:
            supplied = (auth_token or "").strip()
            if supplied != expected:
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
                    allowed = CHANNEL_PERMS.get(channel)
                    if allowed and role not in allowed and not is_super:
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
        )
    except PermissionError as e:
        await websocket.close(code=4429, reason=str(e)[:120])
        return
    try:
        await _ws_loop(channel, websocket)
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
