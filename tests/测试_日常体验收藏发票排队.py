"""日常体验三件套：菜品收藏、电子发票、到店排队（含生日资料与 config 下发）。"""
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_customer, get_current_user, get_db
from app.api import queue as queue_module
from app.core.response import now_local
from app.main import app
from app.models import (
    Customer,
    CustomerFavoriteProduct,
    InvoiceRequest,
    Order,
    Product,
    ProductCategory,
    QueueTicket,
    Store,
    SystemSetting,
)
from app.services.system_settings_service import SystemSettingsService


# ---------------------------------------------------------------------------
# 基础夹具与工具
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def store(db_session: AsyncSession) -> Store:
    s = Store(id=9101, store_no="DAILY01", name="日常体验店", status=1)
    db_session.add(s)
    await db_session.flush()
    return s


async def _add_customer(db_session: AsyncSession, no: str) -> Customer:
    c = Customer(customer_no=no, status=1)
    db_session.add(c)
    await db_session.flush()
    return c


def _super_user():
    role = SimpleNamespace(code="SUPER_ADMIN")
    return SimpleNamespace(id=9901, role_id=1, role=role, store_id=None, status=1, token_version=0)


@asynccontextmanager
async def api_client(db_session: AsyncSession, customer: Customer | None = None, staff=None):
    """sqlite 会话 + 顾客/员工鉴权 mock 的 API 客户端（退出时清理全部覆盖）。"""

    async def override_db():
        yield db_session

    async def override_customer():
        # 共享 session 下，前一请求的 rollback 会 expire fixture 顾客对象；
        # 经 identity key 重取，避免过期属性在同步路径触发 IO（MissingGreenlet）
        cid = inspect(customer).identity[0]
        return await db_session.get(Customer, cid)

    async def override_user():
        return staff or _super_user()

    app.dependency_overrides[get_db] = override_db
    if customer is not None:
        app.dependency_overrides[get_current_customer] = override_customer
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _add_product(db_session: AsyncSession, store: Store, idx: int, **kw) -> Product:
    p = Product(
        id=9200 + idx,
        store_id=store.id,
        category_id=kw.pop("category_id", None) or _ensure_category(db_session, store, idx).id,
        product_no=f"DAILY-P-{idx:03d}",
        name=kw.pop("name", f"牛肉面{idx}号"),
        price=kw.pop("price", Decimal("18.00")),
        status=kw.pop("status", "ON_SALE"),
        stock=kw.pop("stock", 99),
    )
    for k, v in kw.items():
        setattr(p, k, v)
    db_session.add(p)
    return p


def _ensure_category(db_session: AsyncSession, store: Store, idx: int) -> ProductCategory:
    cat = ProductCategory(
        id=9101 + idx,
        category_no=f"DAILY-C{idx}",
        store_id=store.id,
        name=f"分类{idx}",
        sort_order=0,
        status=1,
    )
    db_session.add(cat)
    return cat


def _add_order(db_session: AsyncSession, customer: Customer, store: Store, idx: int, **kw) -> Order:
    o = Order(
        id=9300 + idx,
        order_no=f"DAILY-ORD-{idx:03d}",
        customer_id=customer.id,
        store_id=store.id,
        status=kw.pop("status", "PAID"),
        payment_status=kw.pop("payment_status", "PAID"),
        channel="WECHAT",
        dining_type="DINE_IN",
        subtotal=kw.pop("subtotal", Decimal("88.50")),
        total_amount=kw.pop("total_amount", Decimal("88.50")),
    )
    for k, v in kw.items():
        setattr(o, k, v)
    db_session.add(o)
    return o


def _body(**kw):
    base = {"title_type": "PERSON", "title": "张先生", "email": "a@b.com"}
    base.update(kw)
    return base


async def _upsert_system_setting(db_session: AsyncSession, key: str, value: str, *, sid: int) -> None:
    """幂等写入 system_settings，并同步 _last_db_cache（避免 invalidate 后仍读旧快照）。"""
    from sqlalchemy import select

    row = await db_session.scalar(select(SystemSetting).where(SystemSetting.setting_key == key))
    if row:
        row.setting_value = value
    else:
        # sqlite 下 BigInteger 主键不自增，需显式 id
        db_session.add(SystemSetting(id=sid, setting_key=key, setting_value=value))
    await db_session.flush()
    SystemSettingsService._patch_last_db_cache({key: value})
    SystemSettingsService.invalidate_cache_local()


async def _enable_queue(db_session: AsyncSession):
    await _upsert_system_setting(db_session, "queue_enabled", "1", sid=9701)


async def _enable_invoice(db_session: AsyncSession):
    """开启电子发票开关（默认关闭，需显式启用）。"""
    await _upsert_system_setting(db_session, "invoice_enabled", "1", sid=9702)


# ---------------------------------------------------------------------------
# 收藏
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_favorites_add_list_delete_idempotent(db_session: AsyncSession, store: Store):
    p1 = _add_product(db_session, store, 1, stock=5)
    _add_product(db_session, store, 2, status="OFF_SALE", name="下架牛肉面")
    store2 = Store(id=9102, store_no="DAILY02", name="二店", status=1)
    db_session.add(store2)
    await db_session.flush()
    _add_product(db_session, store2, 3)
    customer = await _add_customer(db_session, "DAILY-CUST-1")
    await db_session.commit()

    async with api_client(db_session, customer) as ac:
        # 收藏上架餐品
        r = await ac.post(f"/api/miniprogram/favorites/{p1.id}")
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {"favorited": True}
        # 幂等：重复收藏仍 true 且仅一条
        r2 = await ac.post(f"/api/miniprogram/favorites/{p1.id}")
        assert r2.json()["data"] == {"favorited": True}
        cnt = (
            await db_session.execute(
                select(func.count())
                .select_from(CustomerFavoriteProduct)
                .where(CustomerFavoriteProduct.customer_id == customer.id)
            )
        ).scalar_one()
        assert cnt == 1
        # 下架/不存在 → HTTP 404 + 业务码 40404
        for pid in (9202, 99999):
            r = await ac.post(f"/api/miniprogram/favorites/{pid}")
            assert r.status_code == 404
            assert r.json()["code"] == 40404
        # 列表：字段齐全
        r = await ac.get("/api/miniprogram/favorites")
        items = r.json()["data"]
        assert len(items) == 1
        it = items[0]
        assert it["product_id"] == p1.id
        assert it["name"] == "牛肉面1号"
        assert it["price"] == "18.00"
        assert it["store_id"] == store.id
        assert it["is_sold_out"] is False
        # 按门店筛选：无匹配为空
        r = await ac.get("/api/miniprogram/favorites", params={"store_id": store2.id})
        assert r.json()["data"] == []
        # 取消收藏幂等
        r = await ac.delete(f"/api/miniprogram/favorites/{p1.id}")
        assert r.json()["data"] == {"favorited": False}
        r = await ac.delete(f"/api/miniprogram/favorites/{p1.id}")
        assert r.status_code == 200 and r.json()["data"] == {"favorited": False}
        r = await ac.get("/api/miniprogram/favorites")
        assert r.json()["data"] == []


# ---------------------------------------------------------------------------
# 发票
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invoice_create_validation_chain(db_session: AsyncSession, store: Store):
    cust = await _add_customer(db_session, "DAILY-CUST-2")
    other = await _add_customer(db_session, "DAILY-CUST-3")
    paid = _add_order(db_session, cust, store, 1)
    unpaid = _add_order(db_session, cust, store, 2, payment_status="UNPAID", status="PENDING_PAYMENT")
    others_paid = _add_order(db_session, other, store, 3)
    await _enable_invoice(db_session)
    await db_session.commit()

    async with api_client(db_session, cust) as ac:
        # 非本人订单 → 40404
        r = await ac.post("/api/miniprogram/invoices", json=_body(order_id=others_paid.id))
        assert r.json()["code"] == 40404
        # 不存在的订单 → 40404
        r = await ac.post("/api/miniprogram/invoices", json=_body(order_id=99999))
        assert r.json()["code"] == 40404
        # 未支付 → 40010
        r = await ac.post("/api/miniprogram/invoices", json=_body(order_id=unpaid.id))
        assert r.json()["code"] == 40010
        assert r.json()["message"] == "仅已支付订单可开票"
        # COMPANY 缺税号 → 40012
        r = await ac.post(
            "/api/miniprogram/invoices",
            json=_body(order_id=paid.id, title_type="COMPANY"),
        )
        assert r.json()["code"] == 40012
        # 邮箱格式错误 → 40012
        r = await ac.post(
            "/api/miniprogram/invoices",
            json=_body(order_id=paid.id, email="not-an-email"),
        )
        assert r.json()["code"] == 40012
        # 正常提交
        r = await ac.post(
            "/api/miniprogram/invoices",
            json=_body(
                order_id=paid.id,
                title_type="COMPANY",
                tax_no="91330100MA27X8WR0X",
                email="fin@corp.cn",
            ),
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["status"] == "PENDING"
        request_id = data["request_id"]
        # 重复提交 → 40011
        r = await ac.post(
            "/api/miniprogram/invoices",
            json=_body(
                order_id=paid.id,
                title_type="COMPANY",
                tax_no="91330100MA27X8WR0X",
                email="fin@corp.cn",
            ),
        )
        assert r.json()["code"] == 40011
        assert r.json()["message"] == "该订单已申请过发票"
        # 本人列表：金额取订单实付、含订单号/状态
        r = await ac.get("/api/miniprogram/invoices")
        body = r.json()["data"]
        assert body["total"] == 1
        item = body["items"][0]
        assert item["request_id"] == request_id
        assert item["order_no"] == paid.order_no
        assert item["amount"] == "88.50"
        assert item["status"] == "PENDING"
        assert item["tax_no"] == "91330100MA27X8WR0X"


@pytest.mark.asyncio
async def test_invoice_admin_issue_and_reject(db_session: AsyncSession, store: Store):
    cust = await _add_customer(db_session, "DAILY-CUST-4")
    inv1 = InvoiceRequest(
        order_id=9401,
        order_no="DAILY-ORD-901",
        store_id=store.id,
        customer_id=cust.id,
        title_type="PERSON",
        title="李女士",
        email="l@x.com",
        amount=Decimal("50.00"),
        status="PENDING",
    )
    inv2 = InvoiceRequest(
        order_id=9402,
        order_no="DAILY-ORD-902",
        store_id=store.id,
        customer_id=cust.id,
        title_type="PERSON",
        title="王先生",
        email="w@x.com",
        amount=Decimal("60.00"),
        status="PENDING",
    )
    inv3 = InvoiceRequest(
        order_id=9403,
        order_no="DAILY-ORD-903",
        store_id=store.id,
        customer_id=cust.id,
        title_type="PERSON",
        title="赵先生",
        email="z@x.com",
        amount=Decimal("70.00"),
        status="ISSUED",
    )
    db_session.add_all([inv1, inv2, inv3])
    await db_session.commit()

    async with api_client(db_session, cust) as ac:
        # 后台筛选真分页
        r = await ac.get("/api/admin/invoices", params={"status": "PENDING"})
        body = r.json()["data"]
        assert body["total"] == 2 and len(body["items"]) == 2
        r = await ac.get("/api/admin/invoices", params={"store_id": store.id})
        assert r.json()["data"]["total"] == 3
        r = await ac.get(
            "/api/admin/invoices", params={"status": "PENDING", "page": 2, "page_size": 1}
        )
        body = r.json()["data"]
        assert body["total"] == 2 and len(body["items"]) == 1

        # 标记开票
        r = await ac.post(f"/api/admin/invoices/{inv1.id}/issue")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == "ISSUED"
        row = await db_session.get(InvoiceRequest, inv1.id)
        assert row.status == "ISSUED" and row.issued_at is not None
        # 非 PENDING 不可重复操作 → 40013
        r = await ac.post(f"/api/admin/invoices/{inv1.id}/issue")
        assert r.json()["code"] == 40013
        r = await ac.post(f"/api/admin/invoices/{inv3.id}/reject", json={"reason": "信息有误"})
        assert r.json()["code"] == 40013
        # 驳回
        r = await ac.post(f"/api/admin/invoices/{inv2.id}/reject", json={"reason": "抬头与税号不一致"})
        assert r.json()["data"]["status"] == "REJECTED"
        row2 = await db_session.get(InvoiceRequest, inv2.id)
        assert row2.status == "REJECTED" and row2.reject_reason == "抬头与税号不一致"
        # 不存在 → 40404
        r = await ac.post("/api/admin/invoices/99999/issue")
        assert r.json()["code"] == 40404


# ---------------------------------------------------------------------------
# 排队
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_queue_take_idempotent_number_guard_and_cancel(db_session: AsyncSession, store: Store):
    cust1 = await _add_customer(db_session, "DAILY-CUST-5")
    cust2 = await _add_customer(db_session, "DAILY-CUST-6")
    cust3 = await _add_customer(db_session, "DAILY-CUST-7")
    await db_session.commit()

    async with api_client(db_session, cust1) as ac:
        # 开关默认关闭 → 40020
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 2})
        assert r.status_code == 400
        assert r.json()["code"] == 40020
        assert r.json()["message"] == "排队功能未开启"

    await _enable_queue(db_session)

    async with api_client(db_session, cust1) as ac:
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 2})
        assert r.status_code == 200, r.text
        t1 = r.json()["data"]
        assert (t1["number"], t1["ticket_no"], t1["status"], t1["ahead_count"]) == (1, "A001", "WAITING", 0)
        assert t1["people_count"] == 2
        # 幂等：同顾客同店当天再次取号返回原票
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 4})
        t1_again = r.json()["data"]
        assert t1_again["id"] == t1["id"] and t1_again["number"] == 1

    async with api_client(db_session, cust2) as ac:
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 3})
        t2 = r.json()["data"]
        assert (t2["number"], t2["ahead_count"]) == (2, 1)
        # 当前小票
        r = await ac.get("/api/miniprogram/queue/current", params={"store_id": store.id})
        assert r.json()["data"]["id"] == t2["id"]

    # ---- 重号防护：MAX 过期快照撞 UNIQUE，自动重试一次 ----
    await db_session.commit()  # 让已取号票在端内 rollback 后仍存在
    # 端内 rollback 会 expire 共享 session 的 fixture 对象，先固化后续要用的主键
    store_id = store.id
    real_max = queue_module._max_number
    calls = {"n": 0}

    async def stale_max(db, store_id, issue_date):
        calls["n"] += 1
        if calls["n"] == 1:
            return 0  # 漏看已有 1 号，制造 number=1 重号
        return await real_max(db, store_id, issue_date)

    async with api_client(db_session, cust3) as ac:
        with patch.object(queue_module, "_max_number", new=AsyncMock(side_effect=stale_max)):
            r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store_id, "people_count": 1})
        assert r.status_code == 200, r.text
        t3 = r.json()["data"]
        assert calls["n"] >= 2, "应触发一次重号重试"
        assert (t3["number"], t3["ticket_no"]) == (3, "A003")

    # ---- 顾客取消后可重新取号 ----
    async with api_client(db_session, cust2) as ac:
        r = await ac.post(f"/api/miniprogram/queue/{t2['id']}/cancel")
        assert r.json()["data"]["status"] == "CANCELLED"
        r = await ac.get("/api/miniprogram/queue/current", params={"store_id": store_id})
        assert r.json()["data"] is None
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store_id, "people_count": 1})
        t2b = r.json()["data"]
        assert (t2b["number"], t2b["ticket_no"]) == (4, "A004")


@pytest.mark.asyncio
async def test_queue_admin_call_seat_cancel_flow(db_session: AsyncSession, store: Store):
    cust1 = await _add_customer(db_session, "DAILY-CUST-8")
    cust2 = await _add_customer(db_session, "DAILY-CUST-9")
    cust3 = await _add_customer(db_session, "DAILY-CUST-10")
    await _enable_queue(db_session)
    await db_session.commit()

    async with api_client(db_session, cust1) as ac:
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 2})
        ticket1 = r.json()["data"]
    async with api_client(db_session, cust2) as ac:
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 2})
        ticket2 = r.json()["data"]
    # 第三位保持 WAITING，用于当日列表排序断言
    async with api_client(db_session, cust3) as ac:
        r = await ac.post("/api/miniprogram/queue/take", json={"store_id": store.id, "people_count": 1})
        ticket3 = r.json()["data"]

    async with api_client(db_session, cust1) as ac:
        with patch.object(queue_module.ws_manager, "broadcast", new=AsyncMock()) as mock_bc:
            # 叫号：WAITING→CALLED + display 渠道广播
            r = await ac.post(f"/api/admin/queue/{ticket1['id']}/call")
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "CALLED"
            mock_bc.assert_awaited_once()
            args, kwargs = mock_bc.await_args
            assert args[0] == "display" and args[1] == "QUEUE_CALLED"
            assert args[2]["ticket_no"] == ticket1["ticket_no"]
            assert args[2]["store_id"] == store.id
            assert args[2]["number"] == ticket1["number"]
            assert kwargs.get("store_id") == store.id
        row = await db_session.get(QueueTicket, ticket1["id"])
        assert row.status == "CALLED" and row.called_at is not None
        # 已叫号不可重复叫号 → 40021
        r = await ac.post(f"/api/admin/queue/{ticket1['id']}/call")
        assert r.json()["code"] == 40021
        # 落座
        r = await ac.post(f"/api/admin/queue/{ticket1['id']}/seat")
        assert r.json()["data"]["status"] == "SEATED"
        r = await ac.post(f"/api/admin/queue/{ticket1['id']}/seat")
        assert r.json()["code"] == 40021
        # 员工取消等待中的票
        r = await ac.post(f"/api/admin/queue/{ticket2['id']}/cancel")
        assert r.json()["data"]["status"] == "CANCELLED"
        r = await ac.post(f"/api/admin/queue/{ticket2['id']}/cancel")
        assert r.json()["code"] == 40021
        # 当日列表：WAITING 在前按 number 正序
        r = await ac.get("/api/admin/queue/today", params={"store_id": store.id})
        items = r.json()["data"]
        assert len(items) == 3
        assert items[0]["id"] == ticket3["id"] and items[0]["status"] == "WAITING"
        assert [it["number"] for it in items[1:]] == [1, 2]
        # status 过滤
        r = await ac.get("/api/admin/queue/today", params={"store_id": store.id, "status": "CANCELLED"})
        data = r.json()["data"]
        assert len(data) == 1 and data[0]["ticket_no"] == ticket2["ticket_no"]


# ---------------------------------------------------------------------------
# 生日资料
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_birthday_update_validation(db_session: AsyncSession):
    cust = await _add_customer(db_session, "DAILY-CUST-11")
    await db_session.commit()
    tomorrow = (now_local() + timedelta(days=1)).date().isoformat()

    async with api_client(db_session, cust) as ac:
        # 正常设置
        r = await ac.put("/api/miniprogram/member/profile", json={"birthday": "2000-02-29"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["birthday"] == "2000-02-29"
        # 兼容 YYYY-M-D（部分端 picker 不补零）
        r = await ac.put("/api/miniprogram/member/profile", json={"birthday": "1990-5-1"})
        assert r.json()["data"]["birthday"] == "1990-05-01"
        # GET profile 返回生日
        r = await ac.get("/api/miniprogram/customers/me")
        assert r.json()["data"]["birthday"] == "1990-05-01"
        # 斜杠分隔
        r = await ac.patch("/api/miniprogram/member/profile", json={"birthday": "2000/01/01"})
        assert r.json()["data"]["birthday"] == "2000-01-01"
        # 格式错误 → 40030
        r = await ac.patch("/api/miniprogram/member/profile", json={"birthday": "not-a-date"})
        assert r.json()["code"] == 40030
        assert r.json()["message"] == "生日格式不正确"
        # 晚于今天 → 40031
        r = await ac.patch("/api/miniprogram/member/profile", json={"birthday": tomorrow})
        assert r.json()["code"] == 40031
        # 空串清除
        r = await ac.patch("/api/miniprogram/member/profile", json={"birthday": ""})
        assert r.json()["data"]["birthday"] is None
        r = await ac.get("/api/miniprogram/customers/me")
        assert r.json()["data"]["birthday"] is None


# ---------------------------------------------------------------------------
# config 下发
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_config_exposes_three_daily_keys(db_session: AsyncSession):
    await db_session.commit()
    async with api_client(db_session) as ac:
        r = await ac.get("/api/miniprogram/config")
        data = r.json()["data"]
        assert data["queue_enabled"] is False
        assert data["invoice_enabled"] is False
        assert data["enable_social_benefit"] is True
        assert data["enable_marketing_campaigns"] is True
        assert data["pay_success_coupon_name"] == ""
        assert "config_version" in data
        assert isinstance(data["config_version"], int)
        assert data["quick_remark_options"] == [
            "不要香菜", "少油少盐", "不要辣", "微辣", "多加醋", "加份米饭",
        ]

    await _upsert_system_setting(db_session, "queue_enabled", "1", sid=9701)
    await _upsert_system_setting(db_session, "pay_success_coupon_name", "好评有礼券", sid=9703)
    await _upsert_system_setting(db_session, "quick_remark_options", " 微辣 ,免葱,,加冰 ", sid=9704)
    await db_session.commit()
    async with api_client(db_session) as ac:
        r = await ac.get("/api/miniprogram/config")
        data = r.json()["data"]
        assert data["queue_enabled"] is True
        assert data["pay_success_coupon_name"] == "好评有礼券"
        assert data["quick_remark_options"] == ["微辣", "免葱", "加冰"]
