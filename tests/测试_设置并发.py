"""系统设置：校验、缓存失效、seed 竞态、高并发读写测试"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import SystemSetting, VisitorEvent
from app.schemas.settings import BusinessSettingsUpdate, StoreUpdate
from app.services.system_settings_service import BUSINESS_SETTING_META, SystemSettingsService


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    SystemSettingsService.invalidate_cache_local()
    # 测试按函数创建独立事件循环；模块级 asyncio.Lock 必须随 loop 一并重建。
    SystemSettingsService._seed_lock = asyncio.Lock()
    SystemSettingsService._cache_refresh_lock = asyncio.Lock()
    yield
    SystemSettingsService.invalidate_cache_local()
    SystemSettingsService._seed_lock = asyncio.Lock()
    SystemSettingsService._cache_refresh_lock = asyncio.Lock()


@pytest.fixture
async def mem_db():
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_validate_int_bounds():
    with pytest.raises(ValueError, match="须在"):
        SystemSettingsService._validate_value("order_payment_timeout_minutes", "0")
    with pytest.raises(ValueError, match="须在"):
        SystemSettingsService._validate_value("order_payment_timeout_minutes", "1441")
    assert SystemSettingsService._validate_value("order_payment_timeout_minutes", "15") == "15"
    assert SystemSettingsService._validate_value("order_payment_timeout_minutes", "15.0") == "15"


@pytest.mark.asyncio
async def test_validate_float_and_nan():
    with pytest.raises(ValueError):
        SystemSettingsService._validate_value("face_match_threshold", "nan")
    with pytest.raises(ValueError):
        SystemSettingsService._validate_value("face_match_threshold", "")
    with pytest.raises(ValueError, match="须在"):
        SystemSettingsService._validate_value("face_match_threshold", "0.2")
    assert SystemSettingsService._validate_value("face_match_threshold", "0.4") == "0.4"
    assert SystemSettingsService._validate_value("face_match_threshold", "0.48") == "0.48"


def test_store_update_schema():
    with pytest.raises(Exception):
        StoreUpdate(name="   ")
    with pytest.raises(Exception):
        StoreUpdate(status=2)
    ok = StoreUpdate(name=" 总店 ", phone=" 13800138000 ", status=1)
    assert ok.name == "总店"
    assert ok.phone == "13800138000"
    # P3-6: 无效电话号码格式应被拒绝
    with pytest.raises(Exception):
        StoreUpdate(phone="abc123")
    with pytest.raises(Exception):
        StoreUpdate(phone="123")
    # 座机格式应通过
    ok2 = StoreUpdate(phone="010-12345678")
    assert ok2.phone == "010-12345678"


def test_business_update_schema_empty():
    with pytest.raises(Exception):
        BusinessSettingsUpdate(settings={})


@pytest.mark.asyncio
async def test_ensure_seeded_idempotent(mem_db: AsyncSession):
    await SystemSettingsService.ensure_seeded(mem_db)
    await SystemSettingsService.ensure_seeded(mem_db)
    rows = (await mem_db.execute(select(SystemSetting))).scalars().all()
    assert len(rows) == len(BUSINESS_SETTING_META)
    keys = {r.setting_key for r in rows}
    assert keys == set(BUSINESS_SETTING_META.keys())


@pytest.mark.asyncio
async def test_concurrent_seed_same_session_safe(mem_db: AsyncSession):
    await asyncio.gather(*[SystemSettingsService.ensure_seeded(mem_db) for _ in range(20)])
    rows = (await mem_db.execute(select(SystemSetting))).scalars().all()
    assert len(rows) == len(BUSINESS_SETTING_META)


@pytest.mark.asyncio
async def test_update_and_read_cache(mem_db: AsyncSession):
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=1)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            await SystemSettingsService.update_business(mem_db, {"welcome_cooldown_seconds": 120})
            v = await SystemSettingsService.get_int(mem_db, "welcome_cooldown_seconds", 60)
            assert v == 120
            SystemSettingsService._cache = {"welcome_cooldown_seconds": "999"}
            SystemSettingsService._cache_at = time.monotonic()
            SystemSettingsService._cache_ver = 1
            cached = await SystemSettingsService.get_int(mem_db, "welcome_cooldown_seconds", 60)
            assert cached == 999
            SystemSettingsService.invalidate_cache_local()
            v2 = await SystemSettingsService.get_int(mem_db, "welcome_cooldown_seconds", 60)
            assert v2 == 120


@pytest.mark.asyncio
async def test_multi_worker_cache_invalidation_via_version(mem_db: AsyncSession):
    ver = {"n": 0}

    async def remote():
        return ver["n"]

    async def bump():
        ver["n"] += 1
        return ver["n"]

    with patch.object(SystemSettingsService, "_remote_version", side_effect=remote):
        with patch.object(SystemSettingsService, "_bump_remote_version", side_effect=bump):
            await SystemSettingsService.update_business(mem_db, {"face_confirm_frames": 5})
            assert await SystemSettingsService.get_int(mem_db, "face_confirm_frames", 3) == 5

            SystemSettingsService._cache = {"face_confirm_frames": "3"}
            SystemSettingsService._cache_at = time.monotonic()
            SystemSettingsService._cache_ver = 0
            assert await SystemSettingsService.get_int(mem_db, "face_confirm_frames", 3) == 5


@pytest.mark.asyncio
async def test_visitor_retention_side_effect(mem_db: AsyncSession):
    far = datetime.now(timezone.utc) + timedelta(days=30)
    mem_db.add(
        VisitorEvent(
            id=1,
            visitor_no="V-TEST-1",
            camera_id=1,
            event_type="ENTER",
            event_time=datetime.now(timezone.utc),
            expires_at=far,
        )
    )
    await mem_db.flush()
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            await SystemSettingsService.update_business(mem_db, {"visitor_data_retention_days": 1})
    row = (await mem_db.execute(select(VisitorEvent))).scalar_one()
    # SQLite 可能丢掉 tzinfo，用时间戳比较
    assert row.expires_at.replace(tzinfo=timezone.utc).timestamp() < far.timestamp()


@pytest.mark.asyncio
async def test_high_concurrency_mixed_read_write(mem_db: AsyncSession):
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            await SystemSettingsService.ensure_seeded(mem_db)
            errors: list[Exception] = []
            values = list(range(10, 60))
            write_lock = asyncio.Lock()

            async def writer(n: int):
                try:
                    async with write_lock:
                        await SystemSettingsService.update_business(mem_db, {"order_payment_timeout_minutes": n})
                except Exception as e:
                    errors.append(e)

            async def reader():
                try:
                    v = await SystemSettingsService.get_int(mem_db, "order_payment_timeout_minutes", 15)
                    assert 1 <= v <= 1440
                except Exception as e:
                    errors.append(e)

            tasks = []
            for n in values:
                tasks.append(asyncio.create_task(writer(n)))
                tasks.append(asyncio.create_task(reader()))
                tasks.append(asyncio.create_task(reader()))
            await asyncio.gather(*tasks)

            assert not errors
            final = await SystemSettingsService.get_int(mem_db, "order_payment_timeout_minutes", 15)
            assert final in values


@pytest.mark.asyncio
async def test_high_concurrency_separate_sessions():
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    SystemSettingsService.invalidate_cache_local()
    SystemSettingsService._seed_lock = asyncio.Lock()

    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):

            async def seed_once():
                async with Session() as db:
                    await SystemSettingsService.ensure_seeded(db)
                    await db.commit()

            await asyncio.gather(*[seed_once() for _ in range(30)])

            async with Session() as db:
                count = len((await db.execute(select(SystemSetting))).scalars().all())
                assert count == len(BUSINESS_SETTING_META)

            write_lock = asyncio.Lock()

            async def write_once(n: int):
                async with write_lock:
                    async with Session() as db:
                        await SystemSettingsService.update_business(db, {"welcome_cooldown_seconds": n})
                        await db.commit()

            await asyncio.gather(*[write_once(n) for n in range(1, 41)])

            async with Session() as db:
                SystemSettingsService.invalidate_cache_local()
                v = await SystemSettingsService.get_int(db, "welcome_cooldown_seconds", 60)
                assert 1 <= v <= 40

    await engine.dispose()


@pytest.mark.asyncio
async def test_reject_unknown_key(mem_db: AsyncSession):
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            with pytest.raises(ValueError, match="不可修改"):
                await SystemSettingsService.update_business(mem_db, {"jwt_secret": "x"})


@pytest.mark.asyncio
async def test_mask_secret_production():
    from app.api import settings as settings_api
    from app.core.config import Settings

    with patch.object(settings_api, "get_settings", return_value=Settings(app_env="production", jwt_secret="abc")):
        assert "已配置" in settings_api._mask_secret("supersecretvalue", show_tail=4)


@pytest.mark.asyncio
async def test_burst_list_business(mem_db: AsyncSession):
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            results = await asyncio.gather(*[SystemSettingsService.list_business(mem_db) for _ in range(50)])
            assert all(len(r) == len(BUSINESS_SETTING_META) for r in results)


@pytest.mark.asyncio
async def test_extreme_concurrent_get_int(mem_db: AsyncSession):
    """200 并发读同一配置，结果一致且合法。"""
    with patch.object(SystemSettingsService, "_remote_version", new=AsyncMock(return_value=0)):
        with patch.object(SystemSettingsService, "_bump_remote_version", new=AsyncMock(return_value=1)):
            await SystemSettingsService.update_business(mem_db, {"face_match_threshold": 0.82})
            await mem_db.commit()
            SystemSettingsService.invalidate_cache_local()

            # AsyncSession 不能被多个 task 共享；每个并发请求使用独立 Session，
            # 服务层 single-flight 锁仍应保证只有一次 DB 回源。
            session_factory = async_sessionmaker(
                bind=mem_db.bind,
                expire_on_commit=False,
            )

            async def one():
                async with session_factory() as session:
                    return await SystemSettingsService.get_float(
                        session, "face_match_threshold", 0.75
                    )

            vals = await asyncio.gather(*[one() for _ in range(200)])
            assert all(abs(v - 0.82) < 1e-6 for v in vals)
