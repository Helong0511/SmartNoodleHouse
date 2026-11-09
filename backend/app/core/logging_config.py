"""结构化日志配置：控制台 + logs/backend 文件滚动。"""
from __future__ import annotations

import logging
import re
import sys


_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:secret|access_token|refresh_token|api[_-]?key|key|sign|signature|token)=)[^& ]+"
)


def _redact_sensitive_query(value: str) -> str:
    return _SENSITIVE_QUERY_RE.sub(
        lambda match: match.group(1) + "[REDACTED]", value
    )


class SensitiveQueryRedactionFilter(logging.Filter):
    """日志兜底脱敏，避免 URL 查询参数中的凭据进入控制台或轮转文件。"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _redact_sensitive_query(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_info:
            record.exc_text = _redact_sensitive_query(
                logging.Formatter().formatException(record.exc_info)
            )
        elif record.exc_text:
            record.exc_text = _redact_sensitive_query(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from app.core.response import dumps_json, now_local, serialize_dt

        # 业务统一中国时区；日志 ts 与业务时钟一致，便于对照订单时间
        payload = {
            "ts": serialize_dt(now_local()),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if record.exc_info:
            payload["exc"] = _redact_sensitive_query(self.formatException(record.exc_info))
        return dumps_json(payload)


def setup_logging(*, json_logs: bool = False, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(SensitiveQueryRedactionFilter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # httpx 会在 INFO 级别记录完整请求 URL；微信 access_token 接口将 AppSecret 放在 query 中。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # 降低噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    try:
        from app.services.file_log_service import attach_backend_file_handler

        path = attach_backend_file_handler(root)
        # 文件 handler 在此处才创建；必须同样应用脱敏过滤器。
        for configured_handler in root.handlers:
            if not any(isinstance(item, SensitiveQueryRedactionFilter) for item in configured_handler.filters):
                configured_handler.addFilter(SensitiveQueryRedactionFilter())
        logging.getLogger(__name__).info("文件日志目录: %s", path)
    except Exception:
        logging.getLogger(__name__).exception("初始化文件日志失败")
