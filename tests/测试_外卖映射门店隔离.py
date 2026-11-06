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



class _SingleResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _MappingDb:
    def __init__(self, existing):
        self.existing = existing
        self.flushed = False

    async def execute(self, _statement):
        return _SingleResult(self.existing)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_ensure_default_map_rejects_reassigning_existing_platform_sku():
    db = _MappingDb(
        existing=SimpleNamespace(
            product_id=100,
            platform_sku_name="旧餐品",
            platform_spu_code="OLD",
            app_food_code="OLD",
            status=1,
        )
    )
    product = SimpleNamespace(id=200, product_no="NEW", name="新餐品")

    with pytest.raises(AppException) as exc:
        await ProductSyncService._ensure_default_map(
            db,
            store_id=1,
            platform="MEITUAN",
            product=product,
            platform_sku_id="PLATFORM-SKU-100",
        )

    assert exc.value.code == 40002
    assert "已绑定其他本地餐品" in str(exc.value)
    assert db.flushed is False



@pytest.mark.asyncio
async def test_reconcile_stock_honors_requested_platform_filter(monkeypatch):
    from app.services import delivery_sync_handlers

    class _Pairs:
        def all(self):
            return [(7, 99)]

    class _Db:
        async def execute(self, _statement):
            return _Pairs()

    push = AsyncMock(return_value=0)
    monkeypatch.setattr(
        delivery_sync_handlers, "_push_store_product_stock", push
    )

    result = await ProductSyncService.reconcile_stock(
        _Db(), store_id=7, platform="eleme"
    )

    assert result == {"products": 1, "failures": 0, "ok": True}
    push.assert_awaited_once()
    assert push.await_args.kwargs["platform_filter"] == "ELEME"
