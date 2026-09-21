"""服务器侧危险请求检测：模式匹配 SQL 注入 / 路径遍历 / 命令注入 / 漏洞探针。

匹配规则全部以 .lower() 字符串比较，避免正则开销；命中即返回 (True, reason)。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import unquote_plus

from fastapi import Request

# 健康检查与 webhook 回调：不参与检测，避免误杀与性能损耗
_EXEMPT_PATH_PREFIXES = (
    "/api/health",
    "/api/ready",
    "/api/payments/wechat/notify",
    "/api/payments/wechat/refund-notify",
    "/api/payments/alipay/notify",
    "/api/delivery/meituan/notify",
    "/api/delivery/eleme/notify",
)

# SQL 注入特征
_SQL_INJECTION_PATTERNS = (
    "union select",
    "or 1=1",
    "' or '1'='1",
    "or '1'='1",
    ";drop",
    "drop table",
    "xp_cmdshell",
    "information_schema",
    "insert into",
    "select pg_sleep",
    "benchmark(",
    "sleep(",
    "waitfor delay",
)

# 路径遍历特征
_PATH_TRAVERSAL_PATTERNS = (
    "../",
    "..\\",
    "%2e%2f",
    "%2e%2e%2f",
    "/etc/passwd",
    "/proc/self",
    "c:\\windows\\win.ini",
    "c:/windows/win.ini",
)

# 命令注入 / XSS / 模板注入特征
_COMMAND_INJECTION_PATTERNS = (
    "|cmd",
    "$(",
    "%24(",
    "<script",
    "javascript:",
    "onerror=",
    "onload=",
    "onmouseover=",
    "<iframe",
    "document.cookie",
    "window.location",
)

# 已知漏洞扫描探针路径
_VULN_PROBE_PATTERNS = (
    ".env",
    ".git/",
    ".git/config",
    "/vendor/phpunit",
    "/wp-admin",
    "/wp-login",
    "/manager/html",
    "/manager/status",
    "console.firebase",
    "phpmyadmin",
    "/struts/",
    "/solr/",
    "/actuator/env",
    "/.well-known/security",
)


def _is_exempt(path: str) -> bool:
    for prefix in _EXEMPT_PATH_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix):
            return True
    return False


def _match_patterns(haystack: str, patterns: tuple[str, ...]) -> str | None:
    for p in patterns:
        if p in haystack:
            return p
    return None


def _collect_request_text(request: Request) -> str:
    """合并 path / query / 关键 header 用于模式匹配。"""
    path = (request.url.path or "").lower()
    # request.url.query 是原始百分号编码字符串；解码后再匹配，避免 UNION%20SELECT 漏检
    query = unquote_plus(request.url.query or "").lower()
    ua = (request.headers.get("user-agent") or "").lower()
    ref = (request.headers.get("referer") or "").lower()
    return f"{path} {query} {ua} {ref}"


def detect_intrusion(request: Request) -> tuple[bool, str]:
    """检查请求是否为危险请求。

    返回 (True, reason) 表示拦截；返回 (False, "") 表示放行。
    所有匹配基于小写字符串包含，无正则，开销低。
    """
    path = (request.url.path or "").lower()
    if _is_exempt(path):
        return False, ""

    haystack = _collect_request_text(request)

    # SQL 注入
    hit = _match_patterns(haystack, _SQL_INJECTION_PATTERNS)
    if hit:
        return True, f"sql_injection:{hit}"

    # 路径遍历
    hit = _match_patterns(haystack, _PATH_TRAVERSAL_PATTERNS)
    if hit:
        return True, f"path_traversal:{hit}"

    # 命令注入 / XSS
    hit = _match_patterns(haystack, _COMMAND_INJECTION_PATTERNS)
    if hit:
        return True, f"command_injection:{hit}"

    # 漏洞探针（仅检查 path）
    hit = _match_patterns(path, _VULN_PROBE_PATTERNS)
    if hit:
        return True, f"vuln_probe:{hit}"

    return False, ""


__all__ = ["detect_intrusion"]
