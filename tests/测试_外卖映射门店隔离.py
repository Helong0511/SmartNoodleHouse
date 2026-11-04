"""外卖商品映射门店隔离回归测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import AppException
from app.services.product_sync_service import ProductSyncService


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, maps, foreign_product_id):
        self.maps = maps
        self.foreign_product_id = foreign_product_id
        self.committed = False

    async def execute(self, _statement):
        return _ScalarRows(self.maps)

    async def scalar(self, _statement):
        return self.foreign_product_id

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_set_shelf_rejects_cross_store_product_mapping_before_platform_call(monkeypatch):
    db = _FakeDb(
        maps=[
            SimpleNamespace(
                product_id=902,
                app_food_code="STORE_B_ITEM",
                platform_sku_id="PLATFORM_SKU_B",
            )
        ],
        foreign_product_id=902,
    )
    credentials = AsyncMock()
    provider_factory = AsyncMock()

    monkeypatch.setattr(
        "app.services.product_sync_service.get_credentials_for_store",
        credentials,
    )
    monkeypatch.setattr(
        "app.services.product_sync_service.get_delivery_provider",
        provider_factory,
    )

    with pytest.raises(AppException) as exc:
        await ProductSyncService.set_shelf(
            db,
            store_id=1,
            platform="MEITUAN",
            on_shelf=False,
        )

    assert exc.value.code == 40301
    assert "跨门店" in str(exc.value)
    assert db.committed is False
    credentials.assert_not_awaited()
    provider_factory.assert_not_awaited()
