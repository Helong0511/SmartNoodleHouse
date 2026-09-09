"""客户端日志上报：前端 / 微信小程序 / 支付宝小程序 → logs/{source}/"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from app.core.response import success
from app.services.file_log_service import write_client_entries

router = APIRouter(prefix="/client-logs", tags=["client-logs"])

ClientSource = Literal["frontend", "wechat", "alipay"]


class ClientLogEntry(BaseModel):
    level: str = "INFO"
    message: str = Field(..., min_length=1, max_length=4000)
    ts: str | None = None
    page: str | None = Field(default=None, max_length=200)
    extra: Any = None

    @field_validator("level")
    @classmethod
    def normalize_level(cls, v: str) -> str:
        return (v or "INFO").strip().upper()[:16]


class ClientLogBatch(BaseModel):
    source: ClientSource
    entries: list[ClientLogEntry] = Field(..., min_length=1, max_length=50)
    device: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=64)


@router.post("")
async def ingest_client_logs(body: ClientLogBatch, request: Request):
    """批量接收客户端日志（无需登录，已限流）。"""
    n = write_client_entries(
        body.source,
        [e.model_dump() for e in body.entries],
        device=body.device,
        app_version=body.app_version,
    )
    return success({"accepted": n}, request=request)
