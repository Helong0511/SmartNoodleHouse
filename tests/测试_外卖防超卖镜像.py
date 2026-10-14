"""外卖防超卖闭环：跨0镜像、信号行复活、乱序/重复收敛、失败重试、门店隔离、40031 拒单。"""
from __future__ import annotations

import itertools
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, text

from app.models import (
    DeliveryOrder,
    DeliveryProductMap,
    DeliverySyncAlert,
    Order,
    OutboxEvent,
    PlatformStoreBinding,
    Product,
    ProductCategory,
    Store,
    StoreProductStock,
)
from app.services import delivery_sync_handlers as hmod
from app.services.delivery_sync_handlers import (
    _push_store_product_stock,
    delivery_stock_mirror_handler,
)
from app.services.stock_service import UNLIMITED_STOCK, deduct_stock, _emit_stock_mirror


# ── 建数辅助 ───────────────────────────────────────────────────────────────
# SQLite 下 BIGINT 主键不自动生成，统一显式分配 id（每个用例独立内存库，无冲突）
_seq = itertools.count(1)


async def _make_store(db, no: str) -> Store:
    s = Store(id=next(_seq), store_no=no, name=no, status=1)
    db.add(s)
    await db.flush()
    return s


async def _make_product(db, store: Store, no: str, price: str = "18.00") -> Product:
    cat = ProductCategory(id=next(_seq), store_id=store.id, category_no=f"{no}-C",
                          name="面食", status=1)
    db.add(cat)
    await db.flush()
    p = Product(
        id=next(_seq), store_id=store.id, category_id=cat.id, product_no=no, name=f"餐品{no}",
        price=Decimal(price), status="ON_SALE", stock=UNLIMITED_STOCK,
    )
    db.add(p)
    await db.flush()
    return p


async def _make_listing(db, store: Store, product: Product, stock: int, status: int = 1):
    row = StoreProductStock(id=next(_seq), store_id=store.id, product_id=product.id,
                            stock=stock, status=status)
    db.add(row)
    await db.flush()
    return row


async def _make_binding_and_map(db, store: Store, product: Product,
                                platform: str = "MEITUAN", sku: str = "SKU1", code: str = "N1"):
    binding = PlatformStoreBinding(
        id=next(_seq), store_id=store.id, platform=platform,
        platform_shop_id=f"{platform}-{store.id}-{next(_seq)}",
        status=1, auto_sync_open=False,
    )
    mapping = DeliveryProductMap(
        id=next(_seq), store_id=store.id, platform=platform, platform_sku_id=sku,
        app_food_code=code, product_id=product.id, status=1,
    )
    db.add_all([binding, mapping])
    await db.flush()
    return binding, mapping


async def _outbox_row(db, mirror_id: int) -> OutboxEvent | None:
    return (
        await db.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "delivery_stock",
                OutboxEvent.aggregate_id == mirror_id,
                OutboxEvent.event_kind == "mirror",
            )
        )
    ).scalar_one_or_none()


def _patch_provider(monkeypatch, fake):
    monkeypatch.setattr(hmod, "get_delivery_provider", lambda platform, creds: fake)

    async def _creds(db, platform, store_id):
        return MagicMock()

    monkeypatch.setattr(hmod, "get_credentials_for_store", _creds)


# ── 跨 0 翻转发信号 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_扣到0发出售罄镜像(db_session):
    store = await _make_store(db_session, "S-SOLD-1")
    product = await _make_product(db_session, store, "P-SOLD-1")
    stock_row = await _make_listing(db_session, store, product, stock=2)
    await db_session.commit()

    await deduct_stock(db_session, product.id, 2, store_id=store.id)

    ob = await _outbox_row(db_session, stock_row.id)
    assert ob is not None and ob.status == "PENDING"
    payload = json.loads(ob.payload)
    assert payload["zero"] is True
    assert payload["store_id"] == store.id and payload["product_id"] == product.id


@pytest.mark.asyncio
async def test_未扣到0不发镜像(db_session):
    store = await _make_store(db_session, "S-SOLD-2")
    product = await _make_product(db_session, store, "P-SOLD-2")
    stock_row = await _make_listing(db_session, store, product, stock=5)
    await db_session.commit()

    await deduct_stock(db_session, product.id, 2, store_id=store.id)
    assert await _outbox_row(db_session, stock_row.id) is None


# ── 信号行复活：同一餐品反复售罄/补货只保留一行 ────────────────────────────

@pytest.mark.asyncio
async def test_补货时已完成信号行被复活而非新增(db_session):
    store = await _make_store(db_session, "S-REV-1")
    product = await _make_product(db_session, store, "P-REV-1")
    stock_row = await _make_listing(db_session, store, product, stock=1)
    await db_session.commit()

    # 售罄 → 产生 PENDING 信号
    await deduct_stock(db_session, product.id, 1, store_id=store.id)
    ob = await _outbox_row(db_session, stock_row.id)
    assert ob is not None and ob.status == "PENDING"

    # 模拟 worker 已消费（DONE）且重试计数累积
    ob.status = "DONE"
    ob.attempt_count = 5
    await db_session.commit()

    # 补货回库（0→1），同事务再次发信号：应复活旧行而不是违反唯一键
    await db_session.execute(
        text("UPDATE store_product_stocks SET stock = 1 WHERE id = :id"),
        {"id": stock_row.id},
    )
    await _emit_stock_mirror(
        db_session, store_id=store.id, product_id=product.id,
        mirror_id=stock_row.id, zero=False,
    )

    rows = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "delivery_stock")
        )
    ).scalars().all()
    assert len(rows) == 1                       # 没有新增第二行
    assert rows[0].status == "PENDING"         # 已复活
    assert rows[0].attempt_count == 0          # 重试计数清零
    assert json.loads(rows[0].payload)["zero"] is False


# ── handler：重读最新库存，乱序/重复都收敛 ─────────────────────────────────

@pytest.mark.asyncio
async def test_handler乱序旧事件仍收敛到最新库存(db_session, monkeypatch):
    store = await _make_store(db_session, "S-HDR-1")
    product = await _make_product(db_session, store, "P-HDR-1")
    stock_row = await _make_listing(db_session, store, product, stock=0)
    _, mapping = await _make_binding_and_map(db_session, store, product, sku="SKUH", code="NH")
    await db_session.commit()

    pushed: list[int] = []

    fake = MagicMock()

    async def _set_sku_stock(items):
        pushed.append(items[0]["stock"])
        return 1

    fake.set_sku_stock = _set_sku_stock
    _patch_provider(monkeypatch, fake)

    # 产生一个 zero=true 的售罄信号行
    await _emit_stock_mirror(
        db_session, store_id=store.id, product_id=product.id,
        mirror_id=stock_row.id, zero=True,
    )
    await db_session.commit()
    ob = await _outbox_row(db_session, stock_row.id)
    stale_row = SimpleNamespace(id=ob.id, payload=ob.payload)  # payload 始终是旧的"售罄"

    # 第一次处理：库存确为 0，推 0
    await delivery_stock_mirror_handler(db_session, stale_row)
    assert pushed == [0]

    # 补货后，即使投递的仍是旧的 zero=true 事件（乱序/重复），也必须推最新值 1
    await db_session.execute(
        text("UPDATE store_product_stocks SET stock = 1 WHERE id = :id"),
        {"id": stock_row.id},
    )
    await db_session.commit()
    db_session.expire_all()  # 模拟 worker 独立事务：必须从数据库重读，而非用会话缓存
    await delivery_stock_mirror_handler(db_session, stale_row)
    await delivery_stock_mirror_handler(db_session, stale_row)  # 重复投递幂等
    assert pushed == [0, 1, 1]
    # 推送给平台的菜品码取自映射
    assert mapping.app_food_code == "NH"


@pytest.mark.asyncio
async def test_handler平台返回失败时抛错等待重试(db_session, monkeypatch):
    store = await _make_store(db_session, "S-HDR-2")
    product = await _make_product(db_session, store, "P-HDR-2")
    stock_row = await _make_listing(db_session, store, product, stock=0)
    await _make_binding_and_map(db_session, store, product, sku="SKUF", code="NF")
    await db_session.commit()

    fake = MagicMock()
    fake.set_sku_stock = AsyncMock(return_value=0)  # 平台成功条数 0
    _patch_provider(monkeypatch, fake)

    await _emit_stock_mirror(
        db_session, store_id=store.id, product_id=product.id,
        mirror_id=stock_row.id, zero=True,
    )
    await db_session.commit()
    ob = await _outbox_row(db_session, stock_row.id)

    with pytest.raises(RuntimeError):
        await delivery_stock_mirror_handler(
            db_session, SimpleNamespace(id=ob.id, payload=ob.payload)
        )


# ── 库存映射：不限=9999、门店停售=0 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_推送库存映射不限与停售(db_session, monkeypatch):
    store = await _make_store(db_session, "S-MAP-1")
    product = await _make_product(db_session, store, "P-MAP-1")
    stock_row = await _make_listing(db_session, store, product, stock=UNLIMITED_STOCK)
    await _make_binding_and_map(db_session, store, product, sku="SKUM", code="NM")
    await db_session.commit()

    seen = []
    fake = MagicMock()

    async def _set(items):
        seen.append(items[0]["stock"])
        return 1

    fake.set_sku_stock = _set
    _patch_provider(monkeypatch, fake)

    assert await _push_store_product_stock(db_session, store.id, product.id) == 0
    assert seen == [9999]  # 本地"不限"映射为平台充足值

    # 门店停售 → 平台售罄 0
    stock_row.status = 0
    await db_session.commit()
    assert await _push_store_product_stock(db_session, store.id, product.id) == 0
    assert seen == [9999, 0]


# ── 门店隔离：只推目标门店绑定，不波及其它门店 ─────────────────────────────

@pytest.mark.asyncio
async def test_库存推送门店隔离(db_session, monkeypatch):
    sa = await _make_store(db_session, "S-ISO-A")
    pa = await _make_product(db_session, sa, "P-ISO-A")
    await _make_listing(db_session, sa, pa, stock=3)
    await _make_binding_and_map(db_session, sa, pa, platform="MEITUAN", sku="SKUA", code="NA")

    sb = await _make_store(db_session, "S-ISO-B")
    pb = await _make_product(db_session, sb, "P-ISO-B")
    await _make_listing(db_session, sb, pb, stock=7)
    await _make_binding_and_map(db_session, sb, pb, platform="ELEME", sku="SKUB", code="NB")
    await db_session.commit()

    pushed_codes = []
    fake = MagicMock()

    async def _set(items):
        pushed_codes.append(items[0]["app_food_code"])
        return 1

    fake.set_sku_stock = _set
    _patch_provider(monkeypatch, fake)

    # 只推 A 店：仅一次且菜品码属于 A，绝不能带上 B 店绑定
    await _push_store_product_stock(db_session, sa.id, pa.id)
    assert pushed_codes == ["NA"]

    await _push_store_product_stock(db_session, sb.id, pb.id)
    assert pushed_codes == ["NA", "NB"]


# ── 40031 跨平台超卖拒单：取消平台单 + 告警，不落本地单 ───────────────────

@pytest.mark.asyncio
async def test_库存不足落单被拒_不建本地单且写告警(db_session):
    from app.services.delivery_service import DeliveryService

    store = await _make_store(db_session, "S-REJ-1")
    product = await _make_product(db_session, store, "P-REJ-1", price="18.00")
    # 门店库存 0（双平台并发把最后几碗卖光）
    await _make_listing(db_session, store, product, stock=0, status=1)
    await _make_binding_and_map(db_session, store, product, sku="SKUR", code="NR")
    await db_session.commit()

    provider = MagicMock()
    provider.cancel_order = AsyncMock(return_value=True)

    parsed = {
        "store_id": store.id,
        "platform": "MEITUAN",
        "platform_order_id": "MT-SOLD-001",
        "total_amount": Decimal("18.00"),
        "items": [
            {"app_food_code": "NR", "platform_sku_id": "SKUR", "quantity": 1,
             "price": Decimal("18.00"), "name": "牛肉面"},
        ],
    }

    order, side = await DeliveryService._create_from_platform(
        db_session, provider, parsed, {}, None
    )

    # 返回 (None, [])：不建本地单
    assert order is None and side == []
    # 已主动取消平台订单
    assert provider.cancel_order.await_count == 1
    assert provider.cancel_order.await_args.args[0] == "MT-SOLD-001"
    # 写入 REJECT_ORDER 告警
    alerts = (
        await db_session.execute(select(DeliverySyncAlert))
    ).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].alert_type == "REJECT_ORDER"
    assert alerts[0].platform_order_id == "MT-SOLD-001"
    assert alerts[0].resolved == 0
    # 本地订单 / 外卖订单扩展均无记录
    assert (await db_session.execute(select(Order))).scalars().all() == []
    assert (await db_session.execute(select(DeliveryOrder))).scalars().all() == []
