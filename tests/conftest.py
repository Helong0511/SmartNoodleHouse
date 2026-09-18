"""测试配置"""
import asyncio
import os
import sys
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
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
def _isolate_system_settings_cache():
    """系统设置为进程内类级缓存（TTL 15s），跨测试残留会造成顺序依赖。

    例如 IP 解析测试通过环境变量配置 TRUSTED_PROXY_IPS，但 ensure_seeded
    类测试可能已把空串 trusted_proxy_ips 留在缓存中，使 resolved_str_sync
    命中空串而非 env 默认值。每个测试前后强制清缓存以隔离。
    """
    from app.services.system_settings_service import SystemSettingsService

    SystemSettingsService.invalidate_cache_local()
    yield
    SystemSettingsService.invalidate_cache_local()
