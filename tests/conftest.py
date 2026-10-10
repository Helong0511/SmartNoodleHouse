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

# 添加 backend 到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import Base
from app.core.response import money
from app.main import app
from app.services.order_service import OrderPriceCalculator

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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

    async def _touch_cache_test(db=None):
        if db is not None:
            await SystemSettingsService._load_raw(db)

    monkeypatch.setattr(SystemSettingsService, "touch_cache", classmethod(_touch_cache_test))
    SystemSettingsService.invalidate_cache_local()
    yield
    SystemSettingsService.invalidate_cache_local()
