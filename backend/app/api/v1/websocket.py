from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_token
from app.services.websocket_manager import ws_manager

router = APIRouter(tags=["WebSocket"])

PROTECTED_CHANNELS = {"kitchen", "orders", "dashboard", "cameras"}


@router.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str, token: str | None = Query(default=None)):
    if channel in PROTECTED_CHANNELS:
        if not token:
            await websocket.close(code=4401, reason="未登录")
            return
        try:
            payload = decode_token(token)
            if payload.get("typ") == "customer" and channel != "orders":
                await websocket.close(code=4403, reason="无权订阅此频道")
                return
        except JWTError:
            await websocket.close(code=4401, reason="无效令牌")
            return

    await ws_manager.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel, websocket)
