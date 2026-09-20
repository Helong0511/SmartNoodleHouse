"""智能推荐相关请求/响应 schema（行为事件、评价、推荐、A/B、隐私）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BehaviorEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=20)
    scene: str | None = Field(default=None, max_length=20)
    product_id: int | None = None
    category_id: int | None = None
    store_id: int | None = None
    ab_bucket: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] | None = None
    occurred_at: datetime | None = None  # 客户端时间仅记录于 payload，服务端以 created_at 为准


class BehaviorEventBatch(BaseModel):
    events: list[BehaviorEventIn] = Field(min_length=1, max_length=50)
