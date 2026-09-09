"""外卖门店凭证：禁止全局 Token 冒充任意门店。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.delivery_context import get_credentials_for_store, resolve_store_and_credentials


@pytest.mark.asyncio
async def test_get_credentials_no_binding_without_fallback():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    with patch("app.services.delivery_context.get_settings") as gs:
        gs.return_value = SimpleNamespace(
            delivery_env_fallback_store_id=0,
            eleme_access_token="tok",
            eleme_shop_id="shop1",
            meituan_shop_id="",
        )
        creds = await get_credentials_for_store(db, "ELEME", store_id=99)
    assert creds is None


@pytest.mark.asyncio
async def test_get_credentials_env_fallback_only_matching_store():
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    with patch("app.services.delivery_context.get_settings") as gs:
        gs.return_value = SimpleNamespace(
            delivery_env_fallback_store_id=1,
            eleme_access_token="tok",
            eleme_shop_id="shop1",
            meituan_shop_id="",
        )
        ok = await get_credentials_for_store(db, "ELEME", store_id=1)
        bad = await get_credentials_for_store(db, "ELEME", store_id=2)
    assert ok is not None and ok.access_token == "tok"
    assert bad is None


@pytest.mark.asyncio
async def test_resolve_ignores_forged_local_store_id():
    from app.core.exceptions import AppException

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    with patch("app.services.delivery_context.get_settings") as gs:
        gs.return_value = SimpleNamespace(
            eleme_shop_id="shop-a",
            eleme_access_token="tok",
            meituan_shop_id="",
            delivery_env_fallback_store_id=0,
        )
        with pytest.raises(AppException) as ei:
            await resolve_store_and_credentials(
                db,
                "ELEME",
                {"shopId": "shop-a", "local_store_id": 999},
                {},
            )
    assert ei.value.code == 40062
