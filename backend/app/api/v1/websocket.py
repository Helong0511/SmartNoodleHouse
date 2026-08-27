from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_token
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["WebSocket"])

PROTECTED_CHANNELS = {"kitchen", "orders", "dashboard", "cameras"}
CHANNEL_PERMS = {
    "kitchen": {"SUPER_ADMIN", "STORE_ADMIN", "KITCHEN", "OPERATOR"},
    "orders": {"SUPER_ADMIN", "STORE_ADMIN", "CASHIER", "OPERATOR", "KITCHEN"},
    "dashboard": {"SUPER_ADMIN", "STORE_ADMIN"},
    "cameras": {"SUPER_ADMIN", "STORE_ADMIN", "OPERATOR"},
}


@router.websocket("/ws/{channel}")
async def websocket_endpoint(
    websocket: WebSocket,
    channel: str,
    token: str | None = Query(default=None),
    store_id: int | None = Query(default=None),
):
    proto = websocket.headers.get("sec-websocket-protocol") or ""
    header_token = None
    if proto:
        parts = [p.strip() for p in proto.split(",")]
        if len(parts) >= 2 and parts[0].lower() in ("bearer", "access_token"):
            header_token = parts[1]
        elif len(parts) == 1 and parts[0].count(".") == 2:
            header_token = parts[0]

    auth_token = header_token or token
    user_store_id = None
    role = None
    is_super = False

    if channel in PROTECTED_CHANNELS:
        if not auth_token:
            await websocket.close(code=4401, reason="未登录")
            return
        try:
            payload = decode_token(auth_token)
            typ = payload.get("typ")
            if typ == "customer":
                if channel != "orders":
                    await websocket.close(code=4403, reason="无权订阅此频道")
                    return
            elif typ != "staff":
                await websocket.close(code=4401, reason="无效令牌类型")
                return
            else:
                role = payload.get("role")
                is_super = role == "SUPER_ADMIN"
                allowed = CHANNEL_PERMS.get(channel)
                if allowed and role not in allowed and not is_super:
                    await websocket.close(code=4403, reason="角色无权订阅")
                    return
                raw_store = payload.get("store_id")
                if raw_store is not None:
                    try:
                        user_store_id = int(raw_store)
                    except (TypeError, ValueError):
                        user_store_id = None
                if not is_super:
                    if not user_store_id:
                        await websocket.close(code=4403, reason="账号未绑定门店")
                        return
                    # 非超管忽略客户端传的 store_id，强制本店
                    store_id = user_store_id
        except JWTError:
            await websocket.close(code=4401, reason="无效令牌")
            return

    effective_store = store_id if is_super else user_store_id
    await ws_manager.connect(
        channel,
        websocket,
        store_id=effective_store,
        is_super_admin=is_super and effective_store is None,
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
