import json
import time
from typing import Set

from fastapi import WebSocket

from app.services.ws_pubsub import publish_ws_event

# 连接配额与空闲超时
MAX_CONNECTIONS_PER_IP = 30
MAX_CONNECTIONS_PER_USER = 8
WS_IDLE_TIMEOUT_SECONDS = 90.0


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
        self._by_ip: dict[str, set[WebSocket]] = {}
        self._by_user: dict[str, set[WebSocket]] = {}

    def _client_ip(self, websocket: WebSocket) -> str:
        client = websocket.client
        return (client.host if client else "") or "unknown"

    def can_accept(self, websocket: WebSocket, *, user_key: str | None = None) -> str | None:
        """返回拒绝原因；None 表示可接受。"""
        ip = self._client_ip(websocket)
        if len(self._by_ip.get(ip, ())) >= MAX_CONNECTIONS_PER_IP:
            return "同一 IP 连接数超限"
        if user_key and len(self._by_user.get(user_key, ())) >= MAX_CONNECTIONS_PER_USER:
            return "同一账号连接数超限"
        return None

    async def connect(
        self,
        channel: str,
        websocket: WebSocket,
        *,
        store_id: int | None = None,
        is_super_admin: bool = False,
        user_key: str | None = None,
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
        ip = self._client_ip(websocket)
        self._by_ip.setdefault(ip, set()).add(websocket)
        if user_key:
            self._by_user.setdefault(user_key, set()).add(websocket)
        self.meta[websocket] = {
            "channel": channel,
            "store_id": store_id,
            "is_super_admin": is_super_admin,
            "keys": keys,
            "ip": ip,
            "user_key": user_key,
            "last_seen": time.monotonic(),
        }

    def touch(self, websocket: WebSocket) -> None:
        meta = self.meta.get(websocket)
        if meta is not None:
            meta["last_seen"] = time.monotonic()

    def disconnect(self, channel: str, websocket: WebSocket):
        meta = self.meta.pop(websocket, {}) or {}
        for key in meta.get("keys") or []:
            if key in self.active:
                self.active[key].discard(websocket)
        ip = meta.get("ip")
        if ip and ip in self._by_ip:
            self._by_ip[ip].discard(websocket)
            if not self._by_ip[ip]:
                self._by_ip.pop(ip, None)
        user_key = meta.get("user_key")
        if user_key and user_key in self._by_user:
            self._by_user[user_key].discard(websocket)
            if not self._by_user[user_key]:
                self._by_user.pop(user_key, None)

    def cleanup_idle(self, idle_seconds: float = WS_IDLE_TIMEOUT_SECONDS) -> list[WebSocket]:
        """返回超时连接（调用方负责 close）。"""
        now = time.monotonic()
        dead: list[WebSocket] = []
        for ws, meta in list(self.meta.items()):
            if now - float(meta.get("last_seen") or now) > idle_seconds:
                dead.append(ws)
        return dead

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

    async def broadcast(
        self,
        channel: str,
        event: str,
        data: dict,
        store_id: int | None = None,
        *,
        local_only: bool = False,
    ):
        """local_only=True：仅本进程推送（Redis 订阅回调使用，避免回环）。"""
        await self.local_broadcast(channel, event, data, store_id=store_id)
        if not local_only:
            await publish_ws_event(channel, event, data, store_id=store_id)

    async def notify_order_event(self, event: str, order_data: dict):
        store_id = order_data.get("store_id")
        await self.broadcast("kitchen", event, order_data, store_id=store_id)
        await self.broadcast("orders", event, order_data, store_id=store_id)
        await self.broadcast("display", event, order_data, store_id=store_id)


ws_manager = ConnectionManager()


async def on_redis_ws_message(channel: str, event: str, data: dict, store_id: int | None):
    await ws_manager.broadcast(channel, event, data, store_id=store_id, local_only=True)
