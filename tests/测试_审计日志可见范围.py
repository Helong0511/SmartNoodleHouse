"""审计日志可见范围回归：非超管不得越权查看本店其他员工的操作记录。

角色语义（audit_logs.py 文件头）：
- SUPER_ADMIN：全库可见
- STORE_ADMIN：本店全部 + 自己操作（含自己 store_id 为空的系统级记录）
- CASHIER/KITCHEN/其他：仅自己操作的记录（且本店）
- 任何非超管：DANGER/INTRUSION 不可见
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OperationLog

# 固定时间，避免依赖默认值；秒数不同不影响断言（排序 id 兜底）
_TS = datetime(2026, 1, 1, 10, 0, 0)

# 人员：100=店长(店1) 200=收银员B(店1) 300=厨房C(店1) 400=收银员D(店2)
_ADMIN = 100
_CASHIER = 200
_KITCHEN = 300
_OTHER_STORE_CASHIER = 400

_STORE1 = 1
_STORE2 = 2

# 预置日志行（id -> 说明）
# 9001 收银员B 本人本店       —— B/厨房? 否；店长可见
# 9002 店A 本人本店          —— 仅店长可见（B 不可见，修复前会泄露）
# 9003 厨房C 本店同事         —— 仅 C 本人与店长可见（B 不可见，修复前会泄露）
# 9004 店2 收银员            —— 店1 所有人不可见
# 9005 收银员B 本人但 store_id=NULL —— 收银员严格 AND 不可见；店长也不可见（非本人非本店）
# 9006 店A 的 DANGER 记录     —— 所有非超管不可见
# 9007 店A 本人 store_id=NULL —— 店长 OR 本人分支可见
def _make_rows() -> list[OperationLog]:
    """每次新建 ORM 实例：db_session 为函数级，detached 实例跨 session 复用会漏插。"""
    return [
        OperationLog(id=9001, user_id=_CASHIER, store_id=_STORE1, category="CASHIER",
                     action="cashier.own", module="pos", detail="{}", created_at=_TS),
        OperationLog(id=9002, user_id=_ADMIN, store_id=_STORE1, category="STORE_ADMIN",
                     action="admin.own", module="store", detail="{}", created_at=_TS),
        OperationLog(id=9003, user_id=_KITCHEN, store_id=_STORE1, category="KITCHEN",
                     action="kitchen.peer", module="kitchen", detail="{}", created_at=_TS),
        OperationLog(id=9004, user_id=_OTHER_STORE_CASHIER, store_id=_STORE2, category="CASHIER",
                     action="other.store", module="pos", detail="{}", created_at=_TS),
        OperationLog(id=9005, user_id=_CASHIER, store_id=None, category="STAFF",
                     action="cashier.nullstore", module="system", detail="{}", created_at=_TS),
        OperationLog(id=9006, user_id=_ADMIN, store_id=_STORE1, category="DANGER",
                     action="admin.danger", module="security", detail="{}", created_at=_TS),
        OperationLog(id=9007, user_id=_ADMIN, store_id=None, category="SYSTEM",
                     action="admin.nullstore", module="system", detail="{}", created_at=_TS),
    ]


def _staff(uid: int, role_code: str, store_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uid, role_id=0, store_id=store_id,
        role=SimpleNamespace(code=role_code), status=1, token_version=0,
    )


async def _fetch_ids(db_session: AsyncSession, user: SimpleNamespace) -> set[int]:
    """以指定身份请求审计日志接口，返回可见记录 id 集合。"""
    from app.api.deps import get_current_user, get_db
    from app.main import app

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"audit:read"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.get("/api/v1/audit-logs", params={"page": 1, "page_size": 100})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["code"] == 0
        return {item["id"] for item in body["data"]["items"]}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cashier_sees_only_own_records_in_store(db_session: AsyncSession):
    """收银员只能看到本人+本店记录：店长/同事/跨店/本人空门店/危险记录均不可见。"""
    db_session.add_all(_make_rows())
    await db_session.flush()

    ids = await _fetch_ids(db_session, _staff(_CASHIER, "CASHIER", _STORE1))
    assert ids == {9001}, f"收银员越权可见：{ids}"


@pytest.mark.asyncio
async def test_kitchen_sees_only_own_records_in_store(db_session: AsyncSession):
    """厨房角色同等严格：仅本人本店记录，看不到收银员/店长操作。"""
    db_session.add_all(_make_rows())
    await db_session.flush()

    ids = await _fetch_ids(db_session, _staff(_KITCHEN, "KITCHEN", _STORE1))
    assert ids == {9003}, f"厨房越权可见：{ids}"


@pytest.mark.asyncio
async def test_store_admin_sees_all_store_records_but_not_danger(db_session: AsyncSession):
    """店长可见本店全部非敏感记录 + 本人空门店记录；跨店、他人空门店、DANGER 不可见。"""
    db_session.add_all(_make_rows())
    await db_session.flush()

    ids = await _fetch_ids(db_session, _staff(_ADMIN, "STORE_ADMIN", _STORE1))
    assert ids == {9001, 9002, 9003, 9007}, f"店长可见范围异常：{ids}"
    assert 9004 not in ids, "店长不得见跨店记录"
    assert 9005 not in ids, "店长不得见他人 store_id 为空的记录"
    assert 9006 not in ids, "DANGER 仅超管可见"


@pytest.mark.asyncio
async def test_super_admin_sees_everything(db_session: AsyncSession):
    """超管（不传门店）可见全部记录，含 DANGER 与跨店。"""
    db_session.add_all(_make_rows())
    await db_session.flush()

    ids = await _fetch_ids(db_session, _staff(1, "SUPER_ADMIN", None))
    assert ids == {9001, 9002, 9003, 9004, 9005, 9006, 9007}


@pytest.mark.asyncio
async def test_cashier_user_id_filter_cannot_bypass_scope(db_session: AsyncSession):
    """收银员即使传 user_id=店长id 也无法绕过可见范围（scope 与筛选条件 AND）。"""
    db_session.add_all(_make_rows())
    await db_session.flush()

    from app.api.deps import get_current_user, get_db
    from app.main import app

    user = _staff(_CASHIER, "CASHIER", _STORE1)

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"audit:read"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.get(
                    "/api/v1/audit-logs",
                    params={"page": 1, "page_size": 100, "user_id": _ADMIN},
                )
        assert res.status_code == 200, res.text
        items = res.json()["data"]["items"]
        assert items == [], "收银员按他人 user_id 筛选必须查不出任何记录"
    finally:
        app.dependency_overrides.clear()
