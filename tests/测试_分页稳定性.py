"""分页稳定性回归：非唯一排序键 OFFSET 翻页重复/丢行；门店停售覆盖导致 total 虚高。

覆盖 P0 修复：
1. 订单/商品在 created_at 或 sort_order 相同的情况下，翻页必须不重复、不丢行（id 兜底）。
2. 商品列表在门店有停售覆盖时，total 必须与实际返回条数一致，每页取满，末页不留空白。
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Order, Product, ProductCategory, Store, StoreProductStock


SAME_TS = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def store(db_session: AsyncSession):
    s = Store(id=8001, store_no="PAGE01", name="分页测试店", status=1)
    db_session.add(s)
    await db_session.flush()
    return s


def _super_user():
    role = SimpleNamespace(code="SUPER_ADMIN")
    return SimpleNamespace(id=9001, role_id=1, role=role, store_id=None, status=1, token_version=0)


async def _fetch_all_pages(client, path, page_size, pages):
    """逐页拉取，返回 (所有 id, total)。"""
    ids: list[int] = []
    total = 0
    for p in range(1, pages + 1):
        res = await client.get(path, params={"page": p, "page_size": page_size})
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        total = data["total"]
        ids.extend(item["id"] for item in data["items"])
    return ids, total


@pytest.mark.asyncio
async def test_orders_same_timestamp_pagination_no_dup_or_loss(db_session: AsyncSession, store):
    """25 单 created_at 完全相同（高峰同秒）：3 页合计必须仍是 25 单，且按 id 降序。"""
    from app.api.deps import get_current_user, get_db
    from app.main import app

    for i in range(1, 26):
        db_session.add(
            Order(
                id=8100 + i,
                order_no=f"PAGE-ORD-{i:03d}",
                store_id=store.id,
                status="PAID",
                payment_status="PAID",
                channel="DINE_IN",
                dining_type="DINE_IN",
                subtotal=Decimal("10.00"),
                total_amount=Decimal("10.00"),
                created_at=SAME_TS,
            )
        )
    await db_session.flush()

    async def override_db():
        yield db_session

    async def override_user():
        return _super_user()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"order:read"})):
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                ids, total = await _fetch_all_pages(ac, "/api/orders", 10, 3)
    finally:
        app.dependency_overrides.clear()

    assert total == 25
    assert len(ids) == 25, f"翻页丢行/重复：实际收集 {len(ids)} 条"
    assert len(set(ids)) == 25, "翻页出现重复订单"
    assert set(ids) == {8101 + i for i in range(25)}
    # created_at 相同时以 id 降序兜底
    assert ids == sorted(ids, reverse=True)


@pytest.mark.asyncio
async def test_products_same_sort_order_pagination_stable(db_session: AsyncSession, store):
    """25 个商品 sort_order 全为默认 0：翻页不重不漏，同 sort_order 内按 id 升序。"""
    from app.api.deps import get_db, get_optional_staff_user
    from app.main import app

    cat = ProductCategory(id=8010, category_no="PAGE-C1", store_id=store.id, name="分页分类", sort_order=0, status=1)
    db_session.add(cat)
    await db_session.flush()
    for i in range(1, 26):
        db_session.add(
            Product(
                id=8200 + i,
                store_id=store.id,
                category_id=cat.id,
                product_no=f"PAGE-P-{i:03d}",
                name=f"分页商品{i}",
                price=Decimal("12.00"),
                status="ON_SALE",
                stock=99,
                sort_order=0,
            )
        )
    await db_session.flush()

    async def override_db():
        yield db_session

    async def override_staff():
        return None

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_staff_user] = override_staff
    try:
        # 公开菜单限流依赖 Redis；测试环境让其 fail-open（与生产 Redis 故障时同一分支）
        with patch(
            "app.api.v1.products.get_redis",
            AsyncMock(side_effect=ConnectionError("redis unavailable in tests")),
        ):
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                ids, total = await _fetch_products(ac, store.id, 3)
    finally:
        app.dependency_overrides.clear()

    assert total == 25
    assert len(ids) == 25
    assert len(set(ids)) == 25
    assert set(ids) == {8201 + i for i in range(25)}
    assert ids == sorted(ids), "同 sort_order 下应按 id 升序兜底"


async def _fetch_products(client, store_id, pages):
    ids: list[int] = []
    total = 0
    for p in range(1, pages + 1):
        res = await client.get(
            "/api/products",
            params={"page": p, "page_size": 10, "store_id": store_id, "status": "ON_SALE"},
        )
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        total = data["total"]
        ids.extend(item["id"] for item in data["items"])
    return ids, total


@pytest.mark.asyncio
async def test_products_store_stop_override_total_matches_rows(db_session: AsyncSession, store):
    """门店停售覆盖 4 个、启用覆盖 2 个：total=26，每页取满，停售品不出现在任何页，启用覆盖库存生效。"""
    from app.api.deps import get_db, get_optional_staff_user
    from app.main import app

    cat = ProductCategory(id=8020, category_no="PAGE-C2", store_id=store.id, name="停售分类", sort_order=0, status=1)
    db_session.add(cat)
    await db_session.flush()
    for i in range(1, 31):  # 30 个商品
        db_session.add(
            Product(
                id=8300 + i,
                store_id=store.id,
                category_id=cat.id,
                product_no=f"PAGE-S-{i:03d}",
                name=f"停售测试商品{i}",
                price=Decimal("15.00"),
                status="ON_SALE",
                stock=99,
                sort_order=i,
            )
        )
    await db_session.flush()
    stopped_ids = {8301, 8302, 8315, 8328}  # 分散在不同页
    enabled_override = {8303: 7, 8320: 3}
    override_id = 0
    for pid in stopped_ids:
        override_id += 1
        db_session.add(StoreProductStock(id=8400 + override_id, store_id=store.id, product_id=pid, stock=0, status=0))
    for pid, stk in enabled_override.items():
        override_id += 1
        db_session.add(
            StoreProductStock(id=8400 + override_id, store_id=store.id, product_id=pid, stock=stk, status=1)
        )
    await db_session.flush()

    async def override_db():
        yield db_session

    async def override_staff():
        return None

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_optional_staff_user] = override_staff

    all_items: list[dict] = []
    total = 0
    try:
        with patch(
            "app.api.v1.products.get_redis",
            AsyncMock(side_effect=ConnectionError("redis unavailable in tests")),
        ):
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                for p in range(1, 4):  # 26 条 / 每页10 = 3 页（10/10/6）
                    res = await ac.get(
                        "/api/products",
                        params={"page": p, "page_size": 10, "store_id": store.id, "status": "ON_SALE"},
                    )
                    assert res.status_code == 200, res.text
                    data = res.json()["data"]
                    total = data["total"]
                    all_items.extend(data["items"])
    finally:
        app.dependency_overrides.clear()

    returned_ids = [it["id"] for it in all_items]
    assert total == 26, f"停售品下推后 total 应为 26，实际 {total}"
    assert len(returned_ids) == 26, "三页合计必须恰好 26 条（修复前会页不满/末页空白）"
    assert len(set(returned_ids)) == 26, "翻页不得重复"
    assert set(returned_ids) == {8301 + i for i in range(30)} - stopped_ids
    assert not (stopped_ids & set(returned_ids)), "本店停售品不得出现在任何页"
    by_id = {it["id"]: it for it in all_items}
    for pid, stk in enabled_override.items():
        assert by_id[pid]["stock"] == stk, "启用状态的库存覆盖值仍应生效"
        assert by_id[pid]["store_stock_override"] is True
