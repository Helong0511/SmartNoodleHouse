"""消息幂等：Redis 加速 + MySQL 事实源；Redis 宕机可降级。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.domains.messaging.idempotency import is_processed, release, try_acquire
from app.models import ProcessedMessage


@pytest.fixture
async def mem_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_try_acquire_dedup_with_mysql_only(mem_db):
    with patch("app.domains.messaging.idempotency.try_get_redis", AsyncMock(return_value=None)):
        assert await try_acquire(mem_db, scope="print", message_id="job:1") is True
        await mem_db.commit()
        assert await try_acquire(mem_db, scope="print", message_id="job:1") is False
        assert await is_processed(mem_db, scope="print", message_id="job:1") is True


@pytest.mark.asyncio
async def test_release_allows_retry(mem_db):
    with patch("app.domains.messaging.idempotency.try_get_redis", AsyncMock(return_value=None)):
        assert await try_acquire(mem_db, scope="outbox", message_id="a:1:k") is True
        await release(mem_db, scope="outbox", message_id="a:1:k")
        await mem_db.commit()
        assert await try_acquire(mem_db, scope="outbox", message_id="a:1:k") is True


@pytest.mark.asyncio
async def test_redis_hit_requires_mysql_confirmation(mem_db):
    """Redis 有键但 DB 无记录视为脏缓存：应清除 Redis 并允许认领（防券卡 RESERVED）。"""
    fake = MagicMock()
    fake.exists = AsyncMock(return_value=1)
    fake.set = AsyncMock()
    fake.delete = AsyncMock()
    fake.ping = AsyncMock(return_value=True)

    with patch("app.domains.messaging.idempotency.try_get_redis", AsyncMock(return_value=fake)):
        # DB 无记录 → 清脏缓存后仍可首次认领
        assert await try_acquire(mem_db, scope="payment", message_id="n1") is True
        await mem_db.commit()
        fake.delete.assert_awaited()
        # 再次认领：DB 已有 → False
        assert await try_acquire(mem_db, scope="payment", message_id="n1") is False


@pytest.mark.asyncio
async def test_is_processed_ignores_redis_only_dirty_key(mem_db):
    """is_processed 不得仅凭 Redis 返回 True（无 MySQL 记录时）。"""
    fake = MagicMock()
    fake.exists = AsyncMock(return_value=1)
    fake.set = AsyncMock()
    fake.delete = AsyncMock()

    with patch("app.domains.messaging.idempotency.try_get_redis", AsyncMock(return_value=fake)):
        assert await is_processed(mem_db, scope="outbox", message_id="paid_side_effect:2:coupon_mark_used") is False


@pytest.mark.asyncio
async def test_rate_limit_fail_open_when_redis_down():
    from starlette.requests import Request

    from app.core.rate_limit import RateLimitMiddleware

    called = {"ok": False}

    async def call_next(_req):
        called["ok"] = True
        return MagicMock(status_code=200)

    mw = RateLimitMiddleware(app=MagicMock())
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/orders",
        "raw_path": b"/api/orders",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    req = Request(scope)
    with patch("app.core.rate_limit.get_redis", AsyncMock(side_effect=RuntimeError("down"))):
        with patch("app.core.rate_limit.get_settings") as gs:
            s = MagicMock()
            s.is_development = False
            s.is_production = True
            s.enable_rate_limit = True
            # 普通业务路径不在 fail-closed 名单；Redis 宕机仍放行
            s.rate_limit_fail_closed_paths = "/api/auth/login,/api/payments/"
            gs.return_value = s
            await mw.dispatch(req, call_next)
    assert called["ok"] is True


@pytest.mark.asyncio
async def test_domain_facades_importable():
    import app.domains.auth  # noqa: F401
    import app.domains.messaging  # noqa: F401
    import app.domains.order  # noqa: F401
    import app.domains.product  # noqa: F401
    import app.domains.user  # noqa: F401
    from app.domains.messaging import idempotency

    assert hasattr(idempotency, "try_acquire")
    assert ProcessedMessage.__tablename__ == "processed_messages"
