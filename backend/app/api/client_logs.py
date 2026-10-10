"""客户端日志上报：前端 / 微信小程序 / 支付宝小程序 → logs/{source}/"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.response import dumps_json, success
from app.services.file_log_service import write_client_entries

router = APIRouter(prefix="/client-logs", tags=["client-logs"])

ClientSource = Literal["frontend", "wechat", "alipay"]

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?i)(authorization|bearer|access_token|customer_token|token|refresh_token|password|secret)"
    r"([\"'\s:=]+)([^\s\"'&,;]{8,})"
)
_MAX_MESSAGE = 2000
_MAX_EXTRA_JSON = 2000
_MAX_ENTRIES = 30
_MAX_BODY_CHARS = 64_000


def _strip_ctrl(value: str) -> str:
    """去掉控制字符（含 \\r\\n），防止日志注入伪造行。"""
    return _CTRL_RE.sub(" ", value or "")


def _redact_secrets(value: str) -> str:
    return _SECRET_RE.sub(r"\1\2***", value or "")


class ClientLogEntry(BaseModel):
    level: str = "INFO"
    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE)
    ts: str | None = Field(default=None, max_length=64)
    page: str | None = Field(default=None, max_length=200)
    extra: Any = None

    @field_validator("level")
    @classmethod
    def normalize_level(cls, v: str) -> str:
        return _strip_ctrl((v or "INFO").strip().upper())[:16]

    @field_validator("message", "ts", "page", mode="before")
    @classmethod
    def clean_text(cls, v: Any) -> Any:
        if v is None:
            return v
        return _redact_secrets(_strip_ctrl(str(v)))

    @field_validator("extra")
    @classmethod
    def limit_extra(cls, v: Any) -> Any:
        if v is None:
            return None
        try:
            raw = dumps_json(v)
        except Exception:
            raw = str(v)
        raw = _redact_secrets(_strip_ctrl(raw))
        if len(raw) > _MAX_EXTRA_JSON:
            return {"_truncated": raw[:_MAX_EXTRA_JSON]}
        try:
            return json.loads(raw)
        except Exception:
            return raw


class ClientLogBatch(BaseModel):
    source: ClientSource
    entries: list[ClientLogEntry] = Field(..., min_length=1, max_length=_MAX_ENTRIES)
    device: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=64)

    @field_validator("device", "app_version", mode="before")
    @classmethod
    def clean_meta(cls, v: Any) -> Any:
        if v is None:
            return v
        return _strip_ctrl(str(v))[:120]

    @model_validator(mode="after")
    def limit_total_body(self) -> ClientLogBatch:
        approx = len(self.model_dump_json())
        if approx > _MAX_BODY_CHARS:
            raise ValueError(f"日志批次过大（>{_MAX_BODY_CHARS} 字符）")
        return self


@router.post("")
async def ingest_client_logs(body: ClientLogBatch, request: Request):
    """批量接收客户端日志（无需登录，已限流；限制条数/消息/控制字符）。"""
    n = write_client_entries(
        body.source,
        [e.model_dump() for e in body.entries],
        device=body.device,
        app_version=body.app_version,
    )
    return success({"accepted": n}, request=request)
