"""测试配置"""
import asyncio
import os
import sys
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

# 添加 backend 到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import Base
from app.core.response import money
from app.main import app
from app.services.order_service import OrderPriceCalculator

# 默认内存 SQLite；CI MySQL job 通过 TEST_DATABASE_URL 注入
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

# 测试中的 pytest-asyncio fixture 使用函数级事件循环；禁用连接池，避免
# AsyncEngine 把前一个 loop 创建的 aiomysql/aiosqlite 连接复用到下一个 loop。
# 每个测试各自创建/销毁 Engine，避免连接跨 pytest event loop 复用。
# SQLite 内存库必须使用 StaticPool 保持同一连接/数据库；MySQL 使用 NullPool，
# 每次借用都新建连接，避免 aiomysql Future 绑定到前一个事件循环。
_TEST_POOL_CLASS = StaticPool if TEST_DATABASE_URL.startswith("sqlite") else NullPool


def _make_sqlite_pk_autoincrement():
    """sqlite 下 `BIGINT PRIMARY KEY` 不是 rowid alias、不会自增（仅 INTEGER PRIMARY KEY 会）。

    将 metadata 中所有 BigInteger 单列主键在 sqlite 方言下转为 Integer，
    使 create_all 的 DDL 变为 INTEGER PRIMARY KEY，恢复原生自增。
    with_variant 只在 sqlite 连接生效，生产 MySQL 不受影响。
    注意：不能用 before_insert 事件分配 MAX(id)+1 —— insertmanyvalues 批量
    INSERT 会在所有 before_insert 之后统一执行，同批多行会分到相同 id。
    """
    from sqlalchemy import Integer

    import app.models  # noqa: F401  触发所有模型注册（函数内 import，避免模块级绑定裸名 app）

    for table in Base.metadata.sorted_tables:
        pk_cols = list(table.primary_key.columns)
        if len(pk_cols) != 1:
            continue
        col = pk_cols[0]
        if not isinstance(col.type, BigInteger):
            continue
        # 已显式 with_variant(Integer,"sqlite") 的列（项目新表约定）跳过
        mapping = getattr(col.type, "_variant_mapping", None)
        if mapping and "sqlite" in mapping:
            continue
        col.type = col.type.with_variant(Integer(), "sqlite")


_make_sqlite_pk_autoincrement()


@pytest.fixture
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(
        TEST_DATABASE_URL, echo=False, poolclass=_TEST_POOL_CLASS
    )
    test_session_local = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with test_session_local() as session:
            yield session
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _isolate_system_settings_cache(monkeypatch):
    """系统设置为进程内类级缓存（TTL 15s），跨测试残留会造成顺序依赖。

    同时屏蔽 touch_cache() 的无 db 分支：该分支用生产 AsyncSessionLocal 兜底，
    在测试进程会连到真实 MySQL 并把生产配置预热进类缓存，污染测试读值
    （CI 无 MySQL 时更会直接报错）。测试环境无 db 上下文时不预热。
    """
    from app.services.system_settings_service import SystemSettingsService

    original_touch_cache = SystemSettingsService.touch_cache.__func__

    async def _touch_cache_test(cls, db=None):
        # 测试不提供 Session 时禁止连接真实数据库；提供 Session 时必须走生产
        # single-flight 实现，避免测试桩绕过锁而掩盖并发回源问题。
        if db is not None:
            await original_touch_cache(cls, db)

    monkeypatch.setattr(SystemSettingsService, "touch_cache", classmethod(_touch_cache_test))
    SystemSettingsService.invalidate_cache_local()
    yield
    SystemSettingsService.invalidate_cache_local()
