"""射洪霸王面限量免费抢购：活动范围、时间基准、暂停语义、抢购资格、核销免单。

阶段7验收：只覆盖本次增量接入的行为，断言统一使用 AppException.message/code。
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    get_current_customer,
    get_current_user,
    get_db,
    get_optional_customer,
)
from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.main import app
from app.models import (
    Customer,
    FlashSaleClaim,
    FlashSaleConfig,
    FlashSaleStock,
    MarketingCampaign,
    MarketingCampaignStore,
    MarketingTask,
    Order,
    OrderItem,
    Payment,
    Permission,
    Product,
    ProductCategory,
    Role,
    RolePermission,
    Store,
)
from app.services.flash_sale_service import FlashSaleService
from app.services.marketing_service import MarketingService


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed(
    db,
    *,
    remaining: int = 2,
    per_customer_limit: int = 1,
    exclude_store_b: bool = False,
):
    """一店/二店 + 总部抢购活动 + 门店库存 + 顾客。"""
    store_a = Store(store_no="FS01", name="抢购一店", status=1)
    store_b = Store(store_no="FS02", name="抢购二店", status=1)
    db.add_all([store_a, store_b])
    await db.flush()
    a_id, b_id = int(store_a.id), int(store_b.id)

    cat_a = ProductCategory(store_id=a_id, category_no="CAT-FS-A", name="霸王面分类")
    cat_b = ProductCategory(store_id=b_id, category_no="CAT-FS-B", name="霸王面分类")
    db.add_all([cat_a, cat_b])
    await db.flush()

    prod_a = Product(
        store_id=a_id, category_id=int(cat_a.id), product_no="P-FS-A",
        name="霸王面（一店）", price=Decimal("18.00"), status="ON_SALE", stock=50,
    )
    prod_b = Product(
        store_id=b_id, category_id=int(cat_b.id), product_no="P-FS-B",
        name="霸王面（二店）", price=Decimal("18.00"), status="ON_SALE", stock=50,
    )
    db.add_all([prod_a, prod_b])
    await db.flush()
    prod_a_id, prod_b_id = int(prod_a.id), int(prod_b.id)

    customer = Customer(
        customer_no="C-FS-1",
        display_name="抢购客",
        member_status="MEMBER",
        account_status="ACTIVE",
        status=1,
        registered_store_id=a_id,
        balance_principal=Decimal("0.00"),
        balance_gift=Decimal("0.00"),
    )
    db.add(customer)
    await db.flush()
    cust_id = int(customer.id)

    now = naive_local_now()
    camp = MarketingCampaign(
        store_id=None,
        code="FS-TEST",
        code_key="HQ:FS-TEST",
        name="射洪霸王面限量免费",
        campaign_type="MEMBER_DAY",
        status="RUNNING",
        schedule_mode="ABSOLUTE",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(days=1),
        published_at=now,
    )
    db.add(camp)
    await db.flush()
    camp_id = int(camp.id)

    cfg = FlashSaleConfig(
        campaign_id=camp_id,
        product_id=prod_a_id,
        per_customer_limit=per_customer_limit,
        claim_valid_minutes=30,
        notice="每人限购，抢完即止",
    )
    stock_a = FlashSaleStock(
        campaign_id=camp_id,
        store_id=a_id,
        product_id=prod_a_id,
        total_quantity=remaining,
        remaining_quantity=remaining,
    )
    stock_b = FlashSaleStock(
        campaign_id=camp_id,
        store_id=b_id,
        product_id=prod_b_id,
        total_quantity=1,
        remaining_quantity=1,
    )
    db.add_all([cfg, stock_a, stock_b])
    if exclude_store_b:
        db.add(MarketingCampaignStore(campaign_id=camp_id, store_id=b_id, included=False))
    await db.commit()
    return {
        "store_a": store_a,
        "store_b": store_b,
        "store_a_id": a_id,
        "store_b_id": b_id,
        "prod_a": prod_a,
        "prod_a_id": prod_a_id,
        "prod_b_id": prod_b_id,
        "customer": customer,
        "customer_id": cust_id,
        "campaign": camp,
        "campaign_id": camp_id,
        "cfg": cfg,
        "stock_a": stock_a,
        "stock_b": stock_b,
    }


async def _remaining(db, campaign_id: int, store_id: int) -> int:
    return int(
        await db.scalar(
            select(FlashSaleStock.remaining_quantity).where(
                FlashSaleStock.campaign_id == int(campaign_id),
                FlashSaleStock.store_id == int(store_id),
            )
        )
        or 0
    )


# ---------------------------------------------------------------- 范围 / 时间 / 暂停


@pytest.mark.asyncio
async def test_hq_campaign_scope_exclusion_and_store_listing(db_session):
    """P0-01：总部活动默认全店参与，被排除门店不能解析/看到/抢购。"""
    seed = await _seed(db_session, exclude_store_b=True)
    camp = seed["campaign"]

    assert await MarketingService.is_store_participating(
        db_session, camp, store_id=seed["store_a_id"]
    )
    assert not await MarketingService.is_store_participating(
        db_session, camp, store_id=seed["store_b_id"]
    )

    running_a = {
        int(c.id)
        for c in await MarketingService.resolve_running_campaigns(
            db_session, seed["store_a_id"]
        )
    }
    running_b = {
        int(c.id)
        for c in await MarketingService.resolve_running_campaigns(
            db_session, seed["store_b_id"]
        )
    }
    assert seed["campaign_id"] in running_a
    assert seed["campaign_id"] not in running_b

    rows_a, total_a = await MarketingService.list_campaigns(
        db_session, store_id=seed["store_a_id"]
    )
    rows_b, total_b = await MarketingService.list_campaigns(
        db_session, store_id=seed["store_b_id"]
    )
    rows_hq, total_hq = await MarketingService.list_campaigns(db_session, store_id=None)
    assert total_a == 1 and total_hq == 1
    assert total_b == 0
    assert seed["campaign_id"] in {int(r["id"]) for r in rows_a}
    assert seed["campaign_id"] in {int(r["id"]) for r in rows_hq}
    assert seed["campaign_id"] not in {int(r["id"]) for r in rows_b}

    active_a = await FlashSaleService.list_active(db_session, store_id=seed["store_a_id"])
    active_b = await FlashSaleService.list_active(db_session, store_id=seed["store_b_id"])
    assert [int(x["campaign_id"]) for x in active_a] == [seed["campaign_id"]]
    assert active_b == []


@pytest.mark.asyncio
async def test_upsert_hq_same_code_not_duplicated(db_session):
    """P0-02：总部活动 store_id=None 时同 code 不能重复创建（应用 + 唯一键兜底）。"""
    first = await MarketingService.upsert_campaign(
        db_session, store_id=None, code="hq_flash", name="总部抢购一"
    )
    await db_session.commit()
    assert first.code_key == "HQ:HQ_FLASH"

    with pytest.raises(AppException) as ei:
        await MarketingService.upsert_campaign(
            db_session, store_id=None, code="HQ_FLASH", name="总部抢购二"
        )
    assert ei.value.code == 40002
    assert "已存在" in ei.value.message

    # 数据库层兜底：绕过服务层直接写入同 code_key 必须被唯一键拦截
    db_session.add(
        MarketingCampaign(
            store_id=None, code="hq_flash", code_key="HQ:HQ_FLASH", name="总部重复"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_paused_keeps_pending_and_worker_skips(db_session):
    """P1-03：暂停保留 PENDING，worker 不消费；恢复 RUNNING 后仍为 PENDING。"""
    seed = await _seed(db_session)
    task = MarketingTask(
        campaign_id=seed["campaign_id"],
        customer_id=seed["customer_id"],
        idempotency_key="fs-task-1",
        status="PENDING",
        action_type="ISSUE_COUPON",
        scheduled_at=None,
    )
    db_session.add(task)
    await db_session.commit()
    task_id = int(task.id)

    await MarketingService.set_status(db_session, seed["campaign_id"], "PAUSED")
    await db_session.commit()
    status = await db_session.scalar(
        select(MarketingTask.status).where(MarketingTask.id == task_id)
    )
    assert status == "PENDING"

    stats = await MarketingService.process_pending_tasks(db_session)
    assert stats["processed"] == 0
    status = await db_session.scalar(
        select(MarketingTask.status).where(MarketingTask.id == task_id)
    )
    assert status == "PENDING"

    await MarketingService.set_status(db_session, seed["campaign_id"], "RUNNING")
    await db_session.commit()
    status = await db_session.scalar(
        select(MarketingTask.status).where(MarketingTask.id == task_id)
    )
    assert status == "PENDING"


@pytest.mark.asyncio
async def test_time_basis_is_beijing(db_session):
    """P0-04：naive_local_now 为北京时间；resolve_running_campaigns 同基准。"""
    now = naive_local_now()
    expect = _utc_naive_now() + timedelta(hours=8)
    assert abs((now - expect).total_seconds()) < 5

    store = Store(store_no="FS-TZ", name="时间基准店", status=1)
    db_session.add(store)
    await db_session.flush()
    utc_now = _utc_naive_now()
    camp = MarketingCampaign(
        store_id=None,
        code="FS-TZ",
        code_key="HQ:FS-TZ",
        name="时间基准活动",
        status="RUNNING",
        schedule_mode="ABSOLUTE",
        # 按 UTC 看尚未开始（+30 分钟），按北京时间看已开始
        start_at=utc_now + timedelta(minutes=30),
        end_at=utc_now + timedelta(days=2),
    )
    db_session.add(camp)
    await db_session.commit()
    rows = await MarketingService.resolve_running_campaigns(db_session, int(store.id))
    assert int(camp.id) in {int(c.id) for c in rows}


# ---------------------------------------------------------------- 抢购资格


@pytest.mark.asyncio
async def test_claim_success_and_request_replay(db_session):
    """正常扣减 + client_request_id 幂等重放不重复扣库存。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    res = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
        client_request_id="req-001",
    )
    await db_session.commit()
    assert res["status"] == "CLAIMED"
    assert res["already_claimed"] is False
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1

    replay = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
        client_request_id="req-001",
    )
    assert replay["already_claimed"] is True
    assert replay["claim_id"] == res["claim_id"]
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1


@pytest.mark.asyncio
async def test_claim_existing_claimed_returns_already(db_session):
    """同一顾客已有未过期资格：再次抢购直接返回原资格，不重复扣减。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    first = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()
    second = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    assert second["already_claimed"] is True
    assert second["claim_id"] == first["claim_id"]
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1


@pytest.mark.asyncio
async def test_claim_per_customer_limit(db_session):
    """已消耗资格（USED）计入限购，达到上限返回 40010。"""
    seed = await _seed(db_session, remaining=3, per_customer_limit=1)
    now = naive_local_now()
    db_session.add(
        FlashSaleClaim(
            campaign_id=seed["campaign_id"],
            store_id=seed["store_a_id"],
            customer_id=seed["customer_id"],
            unit_no=1,
            claim_no="FSC-USED-1",
            status="USED",
            claimed_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=1),
        )
    )
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await FlashSaleService.claim(
            db_session,
            campaign_id=seed["campaign_id"],
            store_id=seed["store_a_id"],
            customer_id=seed["customer_id"],
        )
    assert ei.value.code == 40010
    assert "限购" in ei.value.message
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 3


@pytest.mark.asyncio
async def test_claim_sold_out(db_session):
    """库存为 0 时条件 UPDATE 不命中，返回 40011。"""
    seed = await _seed(db_session, remaining=0, per_customer_limit=1)
    with pytest.raises(AppException) as ei:
        await FlashSaleService.claim(
            db_session,
            campaign_id=seed["campaign_id"],
            store_id=seed["store_a_id"],
            customer_id=seed["customer_id"],
        )
    assert ei.value.code == 40011
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 0


@pytest.mark.asyncio
async def test_claim_excluded_store_rejected(db_session):
    """被排除的门店即使有库存行也不能抢购（40301），库存不变。"""
    seed = await _seed(db_session, exclude_store_b=True)
    with pytest.raises(AppException) as ei:
        await FlashSaleService.claim(
            db_session,
            campaign_id=seed["campaign_id"],
            store_id=seed["store_b_id"],
            customer_id=seed["customer_id"],
        )
    assert ei.value.code == 40301
    assert "不参与" in ei.value.message
    assert await _remaining(db_session, seed["campaign_id"], seed["store_b_id"]) == 1


@pytest.mark.asyncio
async def test_claim_expired_previous_claim_marked_expired(db_session):
    """超时未核销的旧资格置 EXPIRED（名额不返还），新抢购正常扣减。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=2)
    now = naive_local_now()
    old = FlashSaleClaim(
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
        unit_no=1,
        claim_no="FSC-OLD-EXP",
        status="CLAIMED",
        claimed_at=now - timedelta(minutes=40),
        expires_at=now - timedelta(minutes=10),
    )
    db_session.add(old)
    await db_session.commit()
    old_id = int(old.id)

    res = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()
    assert res["status"] == "CLAIMED"
    assert res["claim_id"] != old_id
    assert (
        await db_session.scalar(
            select(FlashSaleClaim.status).where(FlashSaleClaim.id == old_id)
        )
        == "EXPIRED"
    )
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1


# ---------------------------------------------------------------- 核销免单


@pytest.mark.asyncio
async def test_verify_creates_zero_amount_non_revenue_order(db_session):
    """核销生成 0 元免单订单：PAID/FREE/non_revenue，无支付流水、不计营业收入。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()

    out = await FlashSaleService.verify(
        db_session,
        claim_no=claim["claim_no"],
        store_id=seed["store_a_id"],
        operator_id=None,
    )
    await db_session.commit()
    assert out["already_verified"] is False

    order = await db_session.get(Order, int(out["order_id"]))
    assert order.status == "PAID"
    assert order.payment_status == "FREE"
    assert order.payment_method == "FREE"
    assert bool(order.non_revenue) is True
    assert order.total_amount == Decimal("0.00")
    assert int(order.marketing_campaign_id) == seed["campaign_id"]
    assert int(order.flash_claim_id) == int(claim["claim_id"])

    payments = int(
        await db_session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.order_id == int(out["order_id"]))
        )
        or 0
    )
    assert payments == 0

    item = (
        await db_session.execute(
            select(OrderItem).where(OrderItem.order_id == int(out["order_id"]))
        )
    ).scalar_one()
    assert item.unit_price_snapshot == Decimal("0.00")
    assert item.subtotal == Decimal("0.00")

    status = await db_session.scalar(
        select(FlashSaleClaim.status).where(
            FlashSaleClaim.id == int(claim["claim_id"])
        )
    )
    assert status == "USED"
    order_id = await db_session.scalar(
        select(FlashSaleClaim.order_id).where(
            FlashSaleClaim.id == int(claim["claim_id"])
        )
    )
    assert int(order_id) == int(out["order_id"])

    # 核销扣减本店常规餐品库存（门店无覆盖行时扣品牌行；stock=999 视为不限库存）
    await db_session.refresh(seed["prod_a"])
    assert int(seed["prod_a"].stock) == 49


@pytest.mark.asyncio
async def test_verify_duplicate_does_not_create_second_order(db_session):
    """重复核销返回 already_verified，不重复建单。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()
    first = await FlashSaleService.verify(
        db_session,
        claim_no=claim["claim_no"],
        store_id=seed["store_a_id"],
        operator_id=None,
    )
    await db_session.commit()

    second = await FlashSaleService.verify(
        db_session,
        claim_no=claim["claim_no"],
        store_id=seed["store_a_id"],
        operator_id=None,
    )
    await db_session.commit()
    assert second["already_verified"] is True
    assert int(second["order_id"]) == int(first["order_id"])

    count = int(
        await db_session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.flash_claim_id == int(claim["claim_id"]))
        )
        or 0
    )
    assert count == 1


@pytest.mark.asyncio
async def test_verify_cross_store_rejected(db_session):
    """他店资格不允许本店核销（40300）。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await FlashSaleService.verify(
            db_session,
            claim_no=claim["claim_no"],
            store_id=seed["store_b_id"],
            operator_id=None,
        )
    assert ei.value.code == 40300
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_expired_claim_rejected(db_session):
    """过期资格核销返回 40012，并落库为 EXPIRED（名额不返还）。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    now = naive_local_now()
    db_session.add(
        FlashSaleClaim(
            campaign_id=seed["campaign_id"],
            store_id=seed["store_a_id"],
            customer_id=seed["customer_id"],
            unit_no=1,
            claim_no="FSC-VEXP",
            status="CLAIMED",
            claimed_at=now - timedelta(minutes=40),
            expires_at=now - timedelta(minutes=10),
        )
    )
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await FlashSaleService.verify(
            db_session,
            claim_no="FSC-VEXP",
            store_id=seed["store_a_id"],
            operator_id=None,
        )
    assert ei.value.code == 40012
    assert (
        await db_session.scalar(
            select(FlashSaleClaim.status).where(FlashSaleClaim.claim_no == "FSC-VEXP")
        )
        == "EXPIRED"
    )


@pytest.mark.asyncio
async def test_used_claim_counts_against_limit(db_session):
    """核销后的 USED 资格计入限购，再次抢购返回 40010。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()
    await FlashSaleService.verify(
        db_session,
        claim_no=claim["claim_no"],
        store_id=seed["store_a_id"],
        operator_id=None,
    )
    await db_session.commit()

    with pytest.raises(AppException) as ei:
        await FlashSaleService.claim(
            db_session,
            campaign_id=seed["campaign_id"],
            store_id=seed["store_a_id"],
            customer_id=seed["customer_id"],
        )
    assert ei.value.code == 40010
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1


# ---------------------------------------------------------------- 接口层


@asynccontextmanager
async def _api_client(db, *, customer=None, staff=None):
    """sqlite 会话 + 顾客/员工鉴权 mock 的 API 客户端（退出时清理全部覆盖）。"""

    async def override_db():
        yield db

    async def override_customer():
        # 共享 session 下，前一请求的 rollback 会 expire fixture 顾客对象；
        # 经 identity key 重取，避免过期属性在同步路径触发 IO（MissingGreenlet）
        cid = inspect(customer).identity[0]
        return await db.get(Customer, cid)

    async def override_staff():
        return staff

    app.dependency_overrides[get_db] = override_db
    if customer is not None:
        app.dependency_overrides[get_current_customer] = override_customer
        app.dependency_overrides[get_optional_customer] = override_customer
    if staff is not None:
        app.dependency_overrides[get_current_user] = override_staff
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def _role_with_perms(db, *, code: str, perms: list[str]) -> Role:
    role = Role(code=code, name=code)
    db.add(role)
    await db.flush()
    for perm_code in perms:
        perm = await db.scalar(select(Permission).where(Permission.code == perm_code))
        if perm is None:
            perm = Permission(code=perm_code, name=perm_code, module="marketing")
            db.add(perm)
            await db.flush()
        db.add(RolePermission(role_id=int(role.id), permission_id=int(perm.id)))
    await db.commit()
    return role


@pytest.mark.asyncio
async def test_api_customer_list_claim_and_mine(db_session):
    """顾客端接口：列表只含本店可抢活动；抢购幂等重放；我的资格可查询。"""
    seed = await _seed(db_session)
    async with _api_client(db_session, customer=seed["customer"]) as ac:
        r = await ac.get(
            "/api/miniprogram/flash-sales", params={"store_id": seed["store_a_id"]}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        items = body["data"]["items"]
        assert [int(x["campaign_id"]) for x in items] == [seed["campaign_id"]]
        assert int(items[0]["remaining_quantity"]) == 2

        payload = {
            "store_id": seed["store_a_id"],
            "client_request_id": "api-req-1",
        }
        url = f"/api/miniprogram/flash-sales/{seed['campaign_id']}/claim"
        r2 = await ac.post(url, json=payload)
        assert r2.status_code == 200
        first = r2.json()["data"]
        assert first["status"] == "CLAIMED"
        assert first["already_claimed"] is False

        r3 = await ac.post(url, json=payload)
        assert r3.status_code == 200
        replay = r3.json()["data"]
        assert replay["already_claimed"] is True
        assert int(replay["claim_id"]) == int(first["claim_id"])

        r4 = await ac.get(
            "/api/miniprogram/flash-sales/mine", params={"store_id": seed["store_a_id"]}
        )
        assert r4.status_code == 200
        assert len(r4.json()["data"]["items"]) == 1
    assert await _remaining(db_session, seed["campaign_id"], seed["store_a_id"]) == 1


@pytest.mark.asyncio
async def test_api_claim_excluded_store_rejected(db_session):
    """顾客端接口：被排除门店抢购返回 40301，库存不变。"""
    seed = await _seed(db_session, exclude_store_b=True)
    async with _api_client(db_session, customer=seed["customer"]) as ac:
        r = await ac.post(
            f"/api/miniprogram/flash-sales/{seed['campaign_id']}/claim",
            json={"store_id": seed["store_b_id"]},
        )
        assert r.status_code == 403
        assert r.json()["code"] == 40301
    assert await _remaining(db_session, seed["campaign_id"], seed["store_b_id"]) == 1


@pytest.mark.asyncio
async def test_api_admin_hq_requires_super_admin(db_session):
    """后台接口：总部活动的配置/参与范围仅超管可操作（非超管 40301）。"""
    seed = await _seed(db_session)
    role = await _role_with_perms(
        db_session, code="STORE_ADMIN", perms=["coupon:read", "coupon:manage"]
    )
    staff = SimpleNamespace(
        id=9101,
        role_id=int(role.id),
        role=role,
        store_id=seed["store_a_id"],
        status=1,
        token_version=0,
    )
    async with _api_client(db_session, staff=staff) as ac:
        r = await ac.get(f"/api/marketing/flash-sales/campaigns/{seed['campaign_id']}")
        assert r.status_code == 403
        assert r.json()["code"] == 40301
        r2 = await ac.put(
            f"/api/marketing/flash-sales/campaigns/{seed['campaign_id']}/participation",
            json={"excluded_store_ids": []},
        )
        assert r2.status_code == 403
        assert r2.json()["code"] == 40301


@pytest.mark.asyncio
async def test_api_admin_hq_config_and_participation(db_session):
    """后台接口：超管配置参与门店与门店库存配额。"""
    seed = await _seed(db_session)
    staff = SimpleNamespace(
        id=9201,
        role_id=0,
        role=SimpleNamespace(code="SUPER_ADMIN"),
        store_id=None,
        status=1,
        token_version=0,
    )
    async with _api_client(db_session, staff=staff) as ac:
        detail_url = f"/api/marketing/flash-sales/campaigns/{seed['campaign_id']}"
        r = await ac.get(detail_url)
        assert r.status_code == 200
        assert r.json()["data"]["is_hq"] is True

        r2 = await ac.put(
            f"{detail_url}/participation",
            json={"excluded_store_ids": [seed["store_b_id"]]},
        )
        assert r2.status_code == 200
        assert r2.json()["data"]["excluded_store_ids"] == [seed["store_b_id"]]

        r3 = await ac.put(
            f"{detail_url}/config",
            json={
                "product_id": seed["prod_a_id"],
                "per_customer_limit": 2,
                "claim_valid_minutes": 45,
                "store_quotas": [
                    {
                        "store_id": seed["store_a_id"],
                        "product_id": seed["prod_a_id"],
                        "total_quantity": 5,
                    },
                    {
                        "store_id": seed["store_b_id"],
                        "product_id": seed["prod_b_id"],
                        "total_quantity": 3,
                    },
                ],
            },
        )
        assert r3.status_code == 200
        data = r3.json()["data"]
        assert data["config"]["per_customer_limit"] == 2
        assert data["config"]["claim_valid_minutes"] == 45
        stocks = {int(s["store_id"]): s for s in data["stores"]}
        assert stocks[seed["store_a_id"]]["total_quantity"] == 5
        assert stocks[seed["store_a_id"]]["remaining_quantity"] == 5
        assert stocks[seed["store_b_id"]]["total_quantity"] == 3


@pytest.mark.asyncio
async def test_api_verify_ignores_client_store_and_idempotent(db_session, monkeypatch):
    """后台核销：非超管忽略前端 store_id（按绑定门店），重复核销不重复建单、不重复播报。"""
    from unittest.mock import AsyncMock

    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()

    announce = AsyncMock(return_value=None)
    monkeypatch.setattr(FlashSaleService, "announce_verified", announce)

    role = await _role_with_perms(db_session, code="STORE_ADMIN", perms=["coupon:manage"])
    staff = SimpleNamespace(
        id=9301,
        role_id=int(role.id),
        role=role,
        store_id=seed["store_a_id"],
        status=1,
        token_version=0,
    )
    async with _api_client(db_session, staff=staff) as ac:
        payload = {"claim_no": claim["claim_no"], "store_id": seed["store_b_id"]}
        r = await ac.post("/api/marketing/flash-sales/claims/verify", json=payload)
        assert r.status_code == 200
        out = r.json()["data"]
        # 前端传他店 store_id 被忽略，按账号绑定门店 A 核销
        assert int(out["store_id"]) == seed["store_a_id"]
        assert out["already_verified"] is False
        assert announce.await_count == 1

        order = await db_session.get(Order, int(out["order_id"]))
        assert order.payment_status == "FREE"
        assert bool(order.non_revenue) is True

        r2 = await ac.post("/api/marketing/flash-sales/claims/verify", json=payload)
        assert r2.status_code == 200
        again = r2.json()["data"]
        assert again["already_verified"] is True
        assert int(again["order_id"]) == int(out["order_id"])
        # 重复核销不再触发语音播报
        assert announce.await_count == 1


@pytest.mark.asyncio
async def test_api_verify_other_store_rejected(db_session):
    """后台核销：本店账号核销他店资格返回 40300。"""
    seed = await _seed(db_session, remaining=2, per_customer_limit=1)
    claim = await FlashSaleService.claim(
        db_session,
        campaign_id=seed["campaign_id"],
        store_id=seed["store_a_id"],
        customer_id=seed["customer_id"],
    )
    await db_session.commit()

    role = await _role_with_perms(db_session, code="STORE_ADMIN", perms=["coupon:manage"])
    staff = SimpleNamespace(
        id=9401,
        role_id=int(role.id),
        role=role,
        store_id=seed["store_b_id"],
        status=1,
        token_version=0,
    )
    async with _api_client(db_session, staff=staff) as ac:
        r = await ac.post(
            "/api/marketing/flash-sales/claims/verify",
            json={"claim_no": claim["claim_no"]},
        )
        assert r.status_code == 403
        assert r.json()["code"] == 40300
