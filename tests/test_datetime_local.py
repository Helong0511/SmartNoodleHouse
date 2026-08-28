"""本地时间比较工具测试。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from zoneinfo import ZoneInfo

from app.core.response import ensure_local_aware, naive_local_now, now_local
from app.services.platform_token_service import PlatformTokenService


def test_naive_local_now_has_no_tz():
    dt = naive_local_now()
    assert dt.tzinfo is None


def test_ensure_local_aware_allows_compare_with_now_local():
    naive = datetime(2099, 1, 1, 0, 0, 0)
    assert ensure_local_aware(naive) > now_local()


@pytest.mark.asyncio
async def test_platform_token_refresh_skips_valid_naive_expiry(monkeypatch):
    """token_expires_at 为 naive 时不应触发 TypeError。"""
    deadline = now_local() + timedelta(hours=24)
    binding = SimpleNamespace(
        id=1,
        platform="ELEME",
        status=1,
        refresh_token="rt",
        access_token="at",
        token_expires_at=datetime.now() + timedelta(days=30),
    )

    async def fake_execute(stmt):
        result = MagicMock()
        result.scalars.return_value = iter([binding])
        return result

    db = AsyncMock()
    db.execute = fake_execute
    db.commit = AsyncMock()

    monkeypatch.setattr(
        "app.services.platform_token_service.AsyncSessionLocal",
        MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=db), __aexit__=AsyncMock())),
    )
    refreshed = await PlatformTokenService.refresh_expiring_tokens(within_hours=24)
    assert refreshed == 0
    assert deadline  # 仅避免未使用变量告警
