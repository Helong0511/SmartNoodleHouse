import json
from typing import Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, Set[WebSocket]] = {
            "kitchen": set(),
            "orders": set(),
            "dashboard": set(),
        }

    async def connect(self, channel: str, websocket: WebSocket):
        await websocket.accept()
        if channel not in self.active:
            self.active[channel] = set()
        self.active[channel].add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket):
        if channel in self.active:
            self.active[channel].discard(websocket)

    async def broadcast(self, channel: str, event: str, data: dict):
        message = json.dumps({"event": event, "data": data}, default=str)
        dead = []
        for ws in self.active.get(channel, set()):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)

    async def notify_order_event(self, event: str, order_data: dict):
        await self.broadcast("kitchen", event, order_data)
        await self.broadcast("orders", event, order_data)


ws_manager = ConnectionManager()
