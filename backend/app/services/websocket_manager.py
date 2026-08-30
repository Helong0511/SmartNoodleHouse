import json
from typing import Set

from fastapi import WebSocket

from app.services.ws_pubsub import publish_ws_event


class ConnectionManager:
    """
    门店隔离规则：
    - 非超管只加入 channel:{store_id}
    - 超管可加入裸 channel（全店）或 channel:{store_id}
    - 广播：有 store_id 时投 channel:{store_id}；另投裸 channel 给超管
    """

    def __init__(self):
        self.active: dict[str, Set[WebSocket]] = {}
        self.meta: dict[WebSocket, dict] = {}
        self._local_only = False

    async def connect(
        self,
        channel: str,
        websocket: WebSocket,
        *,
        store_id: int | None = None,
        is_super_admin: bool = False,
    ):
        await websocket.accept()
        keys: list[str] = []
        if is_super_admin and store_id is None:
            keys.append(channel)  # 超管看全量
        if store_id is not None:
            keys.append(f"{channel}:{store_id}")
        if not keys:
            # 无门店且非超管：拒绝由上层处理；兜底不入频道
            keys = []
        for key in keys:
            self.active.setdefault(key, set()).add(websocket)
        self.meta[websocket] = {
            "channel": channel,
            "store_id": store_id,
            "is_super_admin": is_super_admin,
            "keys": keys,
        }

    def disconnect(self, channel: str, websocket: WebSocket):
        meta = self.meta.pop(websocket, {}) or {}
        for key in meta.get("keys") or []:
            if key in self.active:
                self.active[key].discard(websocket)

    async def local_broadcast(self, channel: str, event: str, data: dict, store_id: int | None = None):
        message = json.dumps({"event": event, "data": data}, default=str)
        targets: Set[WebSocket] = set()
        # 超管全量频道
        targets |= set(self.active.get(channel, set()))
        if store_id is not None:
            targets |= set(self.active.get(f"{channel}:{store_id}", set()))
        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)

    async def broadcast(self, channel: str, event: str, data: dict, store_id: int | None = None):
        await self.local_broadcast(channel, event, data, store_id=store_id)
        if not self._local_only:
            await publish_ws_event(channel, event, data, store_id=store_id)

    async def notify_order_event(self, event: str, order_data: dict):
        store_id = order_data.get("store_id")
        await self.broadcast("kitchen", event, order_data, store_id=store_id)
        await self.broadcast("orders", event, order_data, store_id=store_id)
        await self.broadcast("display", event, order_data, store_id=store_id)


ws_manager = ConnectionManager()


async def on_redis_ws_message(channel: str, event: str, data: dict, store_id: int | None):
    ws_manager._local_only = True
    try:
        await ws_manager.local_broadcast(channel, event, data, store_id=store_id)
    finally:
        ws_manager._local_only = False
