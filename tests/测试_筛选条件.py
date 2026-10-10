"""P2 筛选条件回归：低库存（门店覆盖 coalesce + 停售 NOT EXISTS 共存）、标签、
订单手机号/收银员、支付金额区间/收银员、持券记录发券门店与券类型。

重点验证新增 outerjoin / where 后 count 与分页结果一致，不扇出、不漏剔。
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CouponTemplate,
    Customer,
    CustomerCoupon,
    Order,
    Payment,
    Product,
    ProductCategory,
    Store,
    StoreProductStock,
    User,
)


def _super_user(uid=9900):
    role = SimpleNamespace(code="SUPER_ADMIN")
    return SimpleNamespace(id=uid, role_id=1, role=role, store_id=None, status=1, token_version=0)


async def _override_app(db_session: AsyncSession, staff=None, user=None):
    from app.api.deps import get_current_user, get_db, get_optional_staff_user
    from app.main import app

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    if staff is not None:
        async def override_staff():
            return staff
        app.dependency_overrides[get_optional_staff_user] = override_staff
    if user is not None:
        async def override_user():
            return user
        app.dependency_overrides[get_current_user] = override_user
    return app


def _clear_overrides():
    from app.main import app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_product_low_stock_store_coalesce_and_tag(db_session: AsyncSession):
    """门店维度低库存：覆盖库存优先 coalesce；停售覆盖仍被 NOT EXISTS 剔除；标签筛选独立生效。"""
    store = Store(id=9100, store_no="FILT-S1", name="筛选店1", status=1)
    cat = ProductCategory(id=9109, category_no="FILT-C1", store_id=9100, name="筛选分类", sort_order=0, status=1)
    db_session.add_all([store, cat])
    # p1 品牌库存 5（低）；p2 品牌 50 但本店覆盖 3（低）；p3 本店停售（必须剔除）；
    # p4 品牌 20（不低）；p5 品牌 99 但热销
    rows = [
        Product(id=9101, store_id=9100, category_id=9109, product_no="F-P-01", name="低库存品牌",
                price=Decimal("10"), status="ON_SALE", stock=5, sort_order=1),
        Product(id=9102, store_id=9100, category_id=9109, product_no="F-P-02", name="低库存覆盖",
                price=Decimal("10"), status="ON_SALE", stock=50, sort_order=2),
        Product(id=9103, store_id=9100, category_id=9109, product_no="F-P-03", name="本店停售",
                price=Decimal("10"), status="ON_SALE", stock=50, sort_order=3),
        Product(id=9104, store_id=9100, category_id=9109, product_no="F-P-04", name="库存充足",
                price=Decimal("10"), status="ON_SALE", stock=20, sort_order=4),
        Product(id=9105, store_id=9100, category_id=9109, product_no="F-P-05", name="热销品",
                price=Decimal("10"), status="ON_SALE", stock=99, sort_order=5, is_hot=1),
    ]
    db_session.add_all(rows)
    db_session.add_all([
        StoreProductStock(id=9120, store_id=9100, product_id=9102, stock=3, status=1),
        StoreProductStock(id=9121, store_id=9100, product_id=9103, stock=0, status=0),
    ])
    await db_session.flush()

    app = await _override_app(db_session, staff=_super_user())
    try:
        with patch(
            "app.api.products.get_redis",
            AsyncMock(side_effect=ConnectionError("redis unavailable in tests")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                low = await ac.get(
                    "/api/products",
                    params={"store_id": 9100, "status": "ON_SALE", "low_stock": "true",
                            "stock_threshold": 10, "page_size": 50},
                )
                hot = await ac.get(
                    "/api/products",
                    params={"store_id": 9100, "status": "ON_SALE", "tag": "HOT", "page_size": 50},
                )
                # 超管全店视角按品牌库存判定：覆盖 3 不应让 p2 命中
                all_low = await ac.get(
                    "/api/products",
                    params={"status": "ON_SALE", "low_stock": "true", "stock_threshold": 10,
                            "page_size": 200},
                )
    finally:
        _clear_overrides()

    assert low.status_code == 200, low.text
    low_data = low.json()["data"]
    low_ids = {it["id"] for it in low_data["items"]}
    assert low_data["total"] == 2, f"低库存应恰好 2 条（品牌5 + 覆盖3），实际 total={low_data['total']}"
    assert low_ids == {9101, 9102}, "停售品须剔除、库存20/99 不命中"
    assert 9103 not in low_ids, "本店停售覆盖不得因 outerjoin 低库存而漏网"

    assert hot.status_code == 200
    hot_ids = {it["id"] for it in hot.json()["data"]["items"]}
    assert 9105 in hot_ids
    assert hot_ids.isdisjoint({9101, 9102, 9103, 9104})

    assert all_low.status_code == 200
    all_low_ids = {it["id"] for it in all_low.json()["data"]["items"]}
    assert 9101 in all_low_ids, "品牌库存 5 应命中全店低库存"
    assert 9102 not in all_low_ids, "全店视角按品牌库存 50 判定，覆盖库存不参与"


@pytest.mark.asyncio
async def test_order_keyword_phone_and_operator_filter(db_session: AsyncSession):
    """订单 keyword 匹配顾客手机号（outerjoin 不扇出、无顾客单不丢）；operator_id 精确过滤。"""
    db_session.add(Store(id=9200, store_no="FILT-S2", name="筛选店2", status=1))
    db_session.add(Customer(id=9201, customer_no="FILT-CU1", phone="13900000001",
                            display_name="王五", status=1))
    db_session.add(User(id=9202, username="filt_cashier", password_hash="x",
                        display_name="收银员小李", role_id=1, store_id=9200, status=1))
    db_session.add_all([
        Order(id=9210, order_no="FILT-O-01", store_id=9200, customer_id=9201, operator_id=9202,
              status="PAID", payment_status="PAID", channel="DINE_IN", dining_type="DINE_IN",
              subtotal=Decimal("10"), total_amount=Decimal("10")),
        Order(id=9211, order_no="FILT-O-02", store_id=9200,
              status="PAID", payment_status="PAID", channel="DINE_IN", dining_type="DINE_IN",
              subtotal=Decimal("10"), total_amount=Decimal("10")),
    ])
    await db_session.flush()

    app = await _override_app(db_session, user=_super_user())
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"order:read"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                by_phone = await ac.get("/api/orders", params={"keyword": "13900000001"})
                by_name = await ac.get("/api/orders", params={"keyword": "王五"})
                by_op = await ac.get("/api/orders", params={"operator_id": 9202})
                no_match = await ac.get("/api/orders", params={"keyword": "13999999999"})
    finally:
        _clear_overrides()

    for res, expect_total, expect_id in [
        (by_phone, 1, 9210),
        (by_name, 1, 9210),
        (by_op, 1, 9210),
        (no_match, 0, None),
    ]:
        assert res.status_code == 200, res.text
        data = res.json()["data"]
        assert data["total"] == expect_total
        if expect_id:
            assert [it["id"] for it in data["items"]] == [expect_id]
            assert data["items"][0]["operator_name"] == "收银员小李"


@pytest.mark.asyncio
async def test_payment_amount_range_and_operator_filter(db_session: AsyncSession):
    """支付流水金额区间与收银员过滤；operator_name 回填。"""
    db_session.add(Store(id=9300, store_no="FILT-S3", name="筛选店3", status=1))
    db_session.add(User(id=9302, username="filt_cashier2", password_hash="x",
                        display_name="收银员小赵", role_id=1, store_id=9300, status=1))
    db_session.add_all([
        Order(id=9310, order_no="FILT-PO-1", store_id=9300, status="PAID", payment_status="PAID",
              channel="DINE_IN", dining_type="DINE_IN",
              subtotal=Decimal("5"), total_amount=Decimal("5")),
        Order(id=9311, order_no="FILT-PO-2", store_id=9300, status="PAID", payment_status="PAID",
              channel="DINE_IN", dining_type="DINE_IN",
              subtotal=Decimal("50"), total_amount=Decimal("50")),
        Payment(id=9320, payment_no="FILT-PAY-1", order_id=9310, operator_id=9302,
                provider="CASH", channel="CASH", amount=Decimal("5.00"), status="PAID"),
        Payment(id=9321, payment_no="FILT-PAY-2", order_id=9311,
                provider="WECHAT", channel="WECHAT", amount=Decimal("50.00"), status="PAID"),
    ])
    await db_session.flush()

    app = await _override_app(db_session, user=_super_user())
    try:
        with patch("app.api.deps.get_user_permissions", AsyncMock(return_value={"payment:read"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                min_res = await ac.get("/api/payments", params={"amount_min": 10})
                range_res = await ac.get("/api/payments",
                                         params={"amount_min": 4, "amount_max": 6})
                op_res = await ac.get("/api/payments", params={"operator_id": 9302})
    finally:
        _clear_overrides()

    assert min_res.status_code == 200, min_res.text
    assert [it["id"] for it in min_res.json()["data"]["items"]] == [9321]
    assert [it["id"] for it in range_res.json()["data"]["items"]] == [9320]
    op_data = op_res.json()["data"]
    assert op_data["total"] == 1
    assert op_data["items"][0]["id"] == 9320
    assert op_data["items"][0]["operator_name"] == "收银员小赵"


@pytest.mark.asyncio
async def test_customer_coupon_issued_store_and_type_filter(db_session: AsyncSession):
    """持券记录：issued_store_id>0 等值、=0 查 IS NULL、coupon_type 过滤。"""
    from app.services.coupon_service import CouponService

    db_session.add(Store(id=9400, store_no="FILT-S4", name="筛选店4", status=1))
    db_session.add_all([
        Customer(id=9401, customer_no="FILT-CC1", status=1),
        Customer(id=9402, customer_no="FILT-CC2", status=1),
        CouponTemplate(id=9410, name="满减券", coupon_type="FIXED",
                       discount_value=Decimal("5.0000"), status=1),
        CouponTemplate(id=9411, name="代金券", coupon_type="VOUCHER",
                       discount_value=Decimal("10.0000"), status=1),
    ])
    future = datetime.now(timezone.utc) + timedelta(days=7)
    db_session.add_all([
        CustomerCoupon(id=9420, coupon_no="FILT-CCN-1", customer_id=9401, template_id=9410,
                       status="AVAILABLE", expires_at=future, issued_at_store_id=9400),
        CustomerCoupon(id=9421, coupon_no="FILT-CCN-2", customer_id=9402, template_id=9411,
                       status="AVAILABLE", expires_at=future, issued_at_store_id=None),
    ])
    await db_session.flush()

    items_store, total_store = await CouponService.list_customer_coupons_admin(
        db_session, issued_store_id=9400, page_size=50
    )
    items_unlimited, total_unlimited = await CouponService.list_customer_coupons_admin(
        db_session, issued_store_id=0, page_size=50
    )
    items_voucher, total_voucher = await CouponService.list_customer_coupons_admin(
        db_session, coupon_type="VOUCHER", page_size=50
    )

    assert total_store == 1 and items_store[0]["coupon_no"] == "FILT-CCN-1"
    assert total_unlimited == 1 and items_unlimited[0]["coupon_no"] == "FILT-CCN-2"
    assert total_voucher == 1 and items_voucher[0]["coupon_no"] == "FILT-CCN-2"
