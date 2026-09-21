"""审计日志：分类推断、门店归属、脱敏、查询可见范围的回归测试。

只测服务层（AuditService），不依赖路由层鉴权 fixture。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.models import OperationLog
from app.services.audit_service import AuditService


def _mock_request(ip: str = "127.0.0.1", ua: str = "pytest", rid: str = "req-1") -> MagicMock:
    """构造满足 AuditService._client_meta 调用的 mock Request。"""
    req = MagicMock()
    req.client = MagicMock(host=ip)
    req.headers = {"user-agent": ua}
    state = MagicMock()
    state.request_id = rid
    req.state = state
    return req


def _fake_user(role_code: str, store_id: int | None = None, user_id: int = 42) -> MagicMock:
    """模拟员工 user 对象，最少字段满足 AuditService.category_for_user 用。"""
    role = MagicMock()
    role.code = role_code
    user = MagicMock()
    user.id = user_id
    user.role = role
    user.store_id = store_id
    return user


# ---------------- 单元测试：分类推断 ----------------


def test_category_for_user_role_mapping():
    """角色 → 审计分类映射：店长/收银员/厨房/系统/普通员工/未知。"""
    cases = [
        ("SUPER_ADMIN", "SYSTEM"),
        ("STORE_ADMIN", "STORE_ADMIN"),
        ("CASHIER", "CASHIER"),
        ("KITCHEN", "KITCHEN"),
        ("OPERATOR", "STAFF"),
        ("UNKNOWN", "STAFF"),  # 未知角色兜底 STAFF
    ]
    for role_code, expected in cases:
        user = _fake_user(role_code)
        assert AuditService.category_for_user(user) == expected, (
            f"role={role_code} 应映射为 {expected}"
        )


def test_category_for_user_none_user():
    """user 为 None 时返回 STAFF 兜底。"""
    assert AuditService.category_for_user(None) == "STAFF"


# ---------------- 集成测试：落库 ----------------


@pytest.mark.asyncio
async def test_log_staff_auto_category_and_store(db_session):
    """log_staff 自动按角色推断分类、按 user.store_id 推断门店归属。"""
    user = _fake_user("STORE_ADMIN", store_id=7)
    req = _mock_request()
    row = await AuditService.log_staff(
        db_session,
        user=user,
        action="table.create",
        module="table",
        target_type="table",
        target_id=999,
        detail={"table_no": "T01"},
        request=req,
    )
    await db_session.flush()
    # 重新查询确保落库
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh is not None
    assert fresh.category == "STORE_ADMIN"
    assert fresh.store_id == 7
    assert fresh.user_id == 42
    assert fresh.action == "table.create"
    assert fresh.module == "table"
    assert fresh.ip == "127.0.0.1"
    assert fresh.request_id == "req-1"
    payload = json.loads(fresh.detail)
    assert payload["table_no"] == "T01"


@pytest.mark.asyncio
async def test_log_staff_override_category_and_store(db_session):
    """log_staff 显式 category/store_id 覆盖自动推断。"""
    user = _fake_user("CASHIER", store_id=5)
    row = await AuditService.log_staff(
        db_session,
        user=user,
        action="system.settings.update",
        module="settings",
        category="SYSTEM",
        store_id=None,  # 系统级跨店操作
        detail={"key": "rate_limit"},
        request=_mock_request(),
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "SYSTEM"
    assert fresh.store_id is None


@pytest.mark.asyncio
async def test_log_staff_cashier_category(db_session):
    """收银员角色自动归类 CASHIER。"""
    user = _fake_user("CASHIER", store_id=3)
    row = await AuditService.log_staff(
        db_session,
        user=user,
        action="pos.shift.open",
        module="pos",
        detail={"shift_no": "S001"},
        request=_mock_request(),
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "CASHIER"
    assert fresh.store_id == 3


@pytest.mark.asyncio
async def test_log_staff_kitchen_category(db_session):
    """厨房角色自动归类 KITCHEN。"""
    user = _fake_user("KITCHEN", store_id=3)
    row = await AuditService.log_staff(
        db_session,
        user=user,
        action="order.status_update",
        module="kitchen",
        target_type="order",
        target_id=88,
        detail={"from": "PENDING", "to": "COOKING"},
        request=_mock_request(),
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "KITCHEN"


# ---------------- 危险 / 渗透记录 ----------------


@pytest.mark.asyncio
async def test_log_intrusion_records_path_and_reason(db_session):
    """log_intrusion 记录路径、原因、分类为 INTRUSION。"""
    row = await AuditService.log_intrusion(
        db_session,
        reason="sql_injection_union_select",
        path="/api/products?sort=UNION%20SELECT",
        request=_mock_request(ip="10.0.0.5"),
        extra={"user_agent": "curl/8.0"},
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "INTRUSION"
    assert fresh.action == "intrusion.detected"
    assert fresh.module == "security"
    assert fresh.ip == "10.0.0.5"
    payload = json.loads(fresh.detail)
    assert payload["reason"] == "sql_injection_union_select"
    assert payload["path"] == "/api/products?sort=UNION%20SELECT"
    assert payload["user_agent"] == "curl/8.0"


@pytest.mark.asyncio
async def test_log_danger_business_abuse(db_session):
    """log_danger 记录业务侧可疑滥用，分类为 DANGER。"""
    row = await AuditService.log_danger(
        db_session,
        action="auth.login_failed_repeated",
        detail={"username": "admin", "failures": 6, "window": "60s"},
        request=_mock_request(),
        user_id=None,
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "DANGER"
    assert fresh.action == "auth.login_failed_repeated"
    assert fresh.user_id is None
    payload = json.loads(fresh.detail)
    assert payload["failures"] == 6


# ---------------- 脱敏 ----------------


def test_redact_masks_sensitive_fields():
    """敏感字段自动脱敏（password/token/phone/id_card 等）。"""
    payload = {
        "username": "alice",
        "password": "secret123",
        "Authorization": "Bearer xxxx",
        "user": {
            "phone": "13800001111",
            "id_card": "510123199001011234",
            "nickname": "Alice",
        },
        "items": [
            {"access_token": "tok-1", "name": "x"},
        ],
    }
    redacted = AuditService.redact(payload)
    assert redacted["username"] == "alice"
    assert redacted["password"] == "***"
    assert redacted["Authorization"] == "***"
    assert redacted["user"]["phone"] == "***"
    assert redacted["user"]["id_card"] == "***"
    assert redacted["user"]["nickname"] == "Alice"
    assert redacted["items"][0]["access_token"] == "***"
    assert redacted["items"][0]["name"] == "x"


def test_redact_truncates_long_strings():
    """超长字符串截断到 2000 字符 + "…"。"""
    long_value = "A" * 3000
    payload = {"detail": long_value}
    redacted = AuditService.redact(payload)
    assert len(redacted["detail"]) == 2001


def test_redact_preserves_nested_lists():
    """嵌套 list 与 dict 应递归脱敏。"""
    payload = {
        "items": [
            {"name": "ok", "token": "leak"},
            [{"inner_token": "x", "v": 1}],
        ]
    }
    redacted = AuditService.redact(payload)
    assert redacted["items"][0]["name"] == "ok"
    assert redacted["items"][0]["token"] == "***"
    assert redacted["items"][1][0]["inner_token"] == "***"
    assert redacted["items"][1][0]["v"] == 1


# ---------------- 登录分类 ----------------


@pytest.mark.asyncio
async def test_login_failed_logs_danger_category(db_session):
    """登录失败记为 DANGER 分类（暴力破解告警入口）。"""
    row = await AuditService.log_login(
        db_session,
        user_id=99,
        username="admin",
        success=False,
        request=_mock_request(),
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "DANGER"
    assert fresh.action == "auth.login_failed"
    assert fresh.user_id is None  # 失败时 user_id 不写入


@pytest.mark.asyncio
async def test_login_success_logs_system_category(db_session):
    """登录成功记为 SYSTEM 分类。"""
    row = await AuditService.log_login(
        db_session,
        user_id=99,
        username="admin",
        success=True,
        request=_mock_request(),
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "SYSTEM"
    assert fresh.action == "auth.login"
    assert fresh.user_id == 99


# ---------------- 顾客分类 ----------------


@pytest.mark.asyncio
async def test_log_face_consent_customer_category(db_session):
    """顾客人脸同意记为 CUSTOMER 分类。"""
    row = await AuditService.log_face_consent(
        db_session,
        customer_id=500,
        profile_id=12,
        request=_mock_request(),
        extra={"consent_text": "v1"},
    )
    await db_session.flush()
    fresh = await db_session.get(OperationLog, row.id)
    assert fresh.category == "CUSTOMER"
    assert fresh.user_id is None
    assert fresh.target_id == 500
    payload = json.loads(fresh.detail)
    assert payload["profile_id"] == 12


# ---------------- 危险请求检测（子代理创建后启用） ----------------


def _make_intrusion_request(path: str, method: str = "GET", headers: dict | None = None):
    """构造一个轻量 mock Request，用于 detect_intrusion。"""
    req = MagicMock()
    req.url = MagicMock()
    req.url.path = path
    req.method = method
    # query 属性被 detect_intrusion 读取时返回的字符串
    req.url.query = path.split("?", 1)[1] if "?" in path else ""
    req.headers = headers or {}
    return req


def test_intrusion_detection_sql_injection_path():
    """SQL 注入路径应被识别为危险。"""
    try:
        from app.core.intrusion_detection import detect_intrusion
    except ImportError:
        pytest.skip("intrusion_detection 模块尚未创建（等待子代理）")
    dangerous_paths = [
        "/api/products?sort=UNION%20SELECT%20password",
        "/api/orders?id=1;DROP%20TABLE%20users",
        "/api/users?name=%27%20OR%20%271%27%3D%271",
        "/api/products?filter=information_schema.tables",
    ]
    for p in dangerous_paths:
        req = _make_intrusion_request(p)
        is_danger, reason = detect_intrusion(req)
        assert is_danger, f"路径 {p} 应被识别为危险（原因：{reason}）"


def test_intrusion_detection_path_traversal():
    """路径遍历应被识别为危险。"""
    try:
        from app.core.intrusion_detection import detect_intrusion
    except ImportError:
        pytest.skip("intrusion_detection 模块尚未创建（等待子代理）")
    dangerous = [
        "/api/files/../../../etc/passwd",
        "/.env",
        "/.git/config",
        "/wp-admin/",
        "/vendor/phpunit/Util/PHP/eval-stdin.php",
    ]
    for p in dangerous:
        req = _make_intrusion_request(p)
        is_danger, _ = detect_intrusion(req)
        assert is_danger, f"路径 {p} 应被识别为危险"


def test_intrusion_detection_allows_normal_requests():
    """正常业务请求不应被误判为危险。"""
    try:
        from app.core.intrusion_detection import detect_intrusion
    except ImportError:
        pytest.skip("intrusion_detection 模块尚未创建（等待子代理）")
    safe = [
        ("/api/products?page=1&page_size=20", "GET"),
        ("/api/orders/123", "GET"),
        ("/api/orders", "POST"),
        ("/api/auth/login", "POST"),
        ("/api/health", "GET"),
        ("/api/audit-logs?page=1&category=STAFF", "GET"),
    ]
    for path, method in safe:
        req = _make_intrusion_request(path, method)
        is_danger, reason = detect_intrusion(req)
        assert not is_danger, f"正常请求 {method} {path} 不应被识别为危险（原因：{reason}）"
