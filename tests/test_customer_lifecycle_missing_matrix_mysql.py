"""Missing MySQL 8 lifecycle-gate race coverage for POS binding and flash claims."""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    Customer,
    CustomerIdentity,
    CustomerWriteGate,
    FlashSaleClaim,
    FlashSaleConfig,
    FlashSaleStock,
    MarketingCampaign,
    Order,
    OrderStatusHistory,
    Payment,
    Product,
    ProductCategory,
    Store,
)
from app.services.customer_merge_service import CustomerMergeService
from app.services.customer_service import CustomerService
from app.services.flash_sale_service import FlashSaleService
from app.services.customer_write_gate import lock_customer_write_gates


async def _seed_flash_claim_race(maker, suffix: str):
    now = naive_local_now()
    async with maker() as setup:
        store = Store(
            store_no=f"FCR-{suffix[:18]}",
            name="Flash claim lifecycle gate race",
            status=1,
        )
        source = Customer(customer_no=f"FCRS-{suffix[:26]}")
        target = Customer(customer_no=f"FCRT-{suffix[:26]}")
        setup.add_all([store, source, target])
        await setup.flush()
        category = ProductCategory(
            store_id=int(store.id),
            category_no=f"FCRC-{suffix[:16]}",
            name="Flash claim concurrency",
            status=1,
        )
        setup.add(category)
        await setup.flush()
        product = Product(
            store_id=int(store.id),
            category_id=int(category.id),
            product_no=f"FCRP-{suffix[:16]}",
            name="Flash claim test product",
            price=Decimal("0.00"),
            stock=5,
            status="ON_SALE",
        )
        setup.add(product)
        await setup.flush()
        campaign = MarketingCampaign(
            store_id=int(store.id),
            code=f"FCR-{suffix[:18]}",
            code_key=f"{int(store.id)}:FCR-{suffix[:18]}",
            name="Flash claim lifecycle gate race",
            campaign_type="FLASH_SALE",
            status="RUNNING",
            start_at=now.replace(hour=0, minute=0, second=0, microsecond=0),
            end_at=now.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=2),
        )
        setup.add(campaign)
        await setup.flush()
        config = FlashSaleConfig(
            campaign_id=int(campaign.id),
            product_id=int(product.id),
            per_customer_limit=1,
            claim_valid_minutes=30,
        )
        stock = FlashSaleStock(
            campaign_id=int(campaign.id),
            store_id=int(store.id),
            product_id=int(product.id),
            total_quantity=1,
            remaining_quantity=1,
        )
        setup.add_all([config, stock])
        await setup.flush()
        ids = {
            "store_id": int(store.id),
            "source_id": int(source.id),
            "target_id": int(target.id),
            "campaign_id": int(campaign.id),
            "category_id": int(category.id),
            "product_id": int(product.id),
        }
        await setup.commit()
    return ids


async def _cleanup_flash_claim_race(maker, ids):
    async with maker() as cleanup:
        await cleanup.execute(
            delete(FlashSaleClaim).where(
                FlashSaleClaim.campaign_id == ids["campaign_id"]
            )
        )
        await cleanup.execute(
            delete(FlashSaleStock).where(
                FlashSaleStock.campaign_id == ids["campaign_id"]
            )
        )
        await cleanup.execute(
            delete(FlashSaleConfig).where(
                FlashSaleConfig.campaign_id == ids["campaign_id"]
            )
        )
        await cleanup.execute(
            delete(MarketingCampaign).where(
                MarketingCampaign.id == ids["campaign_id"]
            )
        )
        await cleanup.execute(delete(Product).where(Product.id == ids["product_id"]))
        await cleanup.execute(
            delete(ProductCategory).where(ProductCategory.id == ids["category_id"])
        )
        await cleanup.execute(
            delete(Customer).where(
                Customer.id.in_([ids["source_id"], ids["target_id"]])
            )
        )
        await cleanup.execute(delete(Store).where(Store.id == ids["store_id"]))
        await cleanup.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_pos_bind_rejects_customer_after_real_merge_commits(db_session):
    """A bind request queued behind merge must re-read and reject the disabled customer."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"PBR-{suffix[:18]}", name="POS bind merge race", status=1)
        source = Customer(customer_no=f"PBRS-{suffix[:25]}")
        target = Customer(customer_no=f"PBRT-{suffix[:25]}")
        setup.add_all([store, source, target])
        await setup.flush()
        order = Order(
            order_no=f"PBR-{suffix[:24]}",
            store_id=int(store.id),
            customer_id=None,
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel="POS",
            dining_type="TAKEAWAY",
            subtotal=Decimal("10.00"),
            discount_amount=Decimal("0.00"),
            coupon_discount=Decimal("0.00"),
            voucher_discount=Decimal("0.00"),
            packing_fee=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )
        setup.add(order)
        await setup.flush()
        ids = {
            "store_id": int(store.id),
            "source_id": int(source.id),
            "target_id": int(target.id),
            "order_id": int(order.id),
        }
        await setup.commit()

    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    from app.api import orders_pos

    async with maker() as merge_db, maker() as bind_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            await CustomerMergeService.merge(
                merge_db,
                target_customer_id=ids["target_id"],
                source_customer_id=ids["source_id"],
                reason="MySQL POS bind wins-after-merge lifecycle regression",
            )

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            with (
                patch.object(orders_pos, "assert_order_store_access", lambda *_: None),
                patch.object(
                    orders_pos,
                    "_recompute_pos_amounts_for_customer",
                    new_callable=AsyncMock,
                ),
                patch.object(
                    orders_pos.AuditService,
                    "log_staff",
                    new_callable=AsyncMock,
                ),
            ):
                bind_task = asyncio.create_task(
                    orders_pos.bind_pos_customer(
                        order_id=ids["order_id"],
                        data=type("BindData", (), {"customer_id": ids["source_id"]})(),
                        request=type("Request", (), {})(),
                        db=bind_db,
                        user=type(
                            "User",
                            (),
                            {"id": 1, "store_id": ids["store_id"]},
                        )(),
                    )
                )
                await asyncio.wait_for(gate_waiting.wait(), timeout=10)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(bind_task, timeout=15)
                assert exc.value.status_code == 404
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if bind_db.in_transaction():
                await bind_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source = await verify.get(Customer, ids["source_id"])
        order = await verify.get(Order, ids["order_id"])
        assert source is not None and source.account_status == "DISABLED"
        assert order is not None and order.customer_id is None
        await verify.execute(delete(Order).where(Order.id == ids["order_id"]))
        await verify.execute(
            delete(Customer).where(
                Customer.id.in_([ids["source_id"], ids["target_id"]])
            )
        )
        await verify.execute(delete(Store).where(Store.id == ids["store_id"]))
        await verify.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_claim_rejects_customer_after_real_merge_commits(db_session):
    """The real claim service must wait behind merge and leave stock/claims unchanged."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    from app.services.flash_sale_service import MarketingService

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    ids = await _seed_flash_claim_race(maker, uuid.uuid4().hex)
    gate_waiting = asyncio.Event()

    def observe_gate_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            gate_waiting.set()

    async with maker() as merge_db, maker() as claim_db:
        with patch(
            "app.services.customer_merge_service.AuditService.log",
            new_callable=AsyncMock,
        ):
            await CustomerMergeService.merge(
                merge_db,
                target_customer_id=ids["target_id"],
                source_customer_id=ids["source_id"],
                reason="MySQL flash claim after-merge lifecycle regression",
            )

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            with (
                patch.object(MarketingService, "is_enabled", new=AsyncMock(return_value=True)),
                patch.object(
                    MarketingService,
                    "is_store_participating",
                    new=AsyncMock(return_value=True),
                ),
            ):
                claim_task = asyncio.create_task(
                    FlashSaleService.claim(
                        claim_db,
                        campaign_id=ids["campaign_id"],
                        store_id=ids["store_id"],
                        customer_id=ids["source_id"],
                        client_request_id=f"fclaim-{uuid.uuid4().hex[:24]}",
                    )
                )
                await asyncio.wait_for(gate_waiting.wait(), timeout=10)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(claim_task, timeout=15)
                assert exc.value.status_code == 401
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            if claim_db.in_transaction():
                await claim_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source = await verify.get(Customer, ids["source_id"])
        stock = await verify.scalar(
            select(FlashSaleStock).where(
                FlashSaleStock.campaign_id == ids["campaign_id"],
                FlashSaleStock.store_id == ids["store_id"],
            )
        )
        claim_count = await verify.scalar(
            select(func.count()).select_from(FlashSaleClaim).where(
                FlashSaleClaim.campaign_id == ids["campaign_id"]
            )
        )
        assert source is not None and source.account_status == "DISABLED"
        assert stock is not None and stock.remaining_quantity == 1
        assert claim_count == 0
    await _cleanup_flash_claim_race(maker, ids)


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_claim_commits_before_real_merge_cancels_entitlement(db_session):
    """A claim that owns the gate first must commit before merge cancels it."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    from app.services.flash_sale_service import MarketingService

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    ids = await _seed_flash_claim_race(maker, uuid.uuid4().hex)
    merge_waiting = asyncio.Event()

    def observe_merge_wait(_conn, _cursor, statement, _parameters, _context, _many):
        normalized = statement.upper()
        if "CUSTOMER_WRITE_GATES" in normalized and "FOR UPDATE" in normalized:
            merge_waiting.set()

    async with maker() as claim_db, maker() as merge_db:
        with (
            patch.object(MarketingService, "is_enabled", new=AsyncMock(return_value=True)),
            patch.object(
                MarketingService,
                "is_store_participating",
                new=AsyncMock(return_value=True),
            ),
        ):
            claimed = await FlashSaleService.claim(
                claim_db,
                campaign_id=ids["campaign_id"],
                store_id=ids["store_id"],
                customer_id=ids["source_id"],
                client_request_id=f"fclaim-{uuid.uuid4().hex[:24]}",
            )
        claim_id = int(claimed["claim_id"])

        event.listen(engine.sync_engine, "before_cursor_execute", observe_merge_wait)
        try:
            with patch(
                "app.services.customer_merge_service.AuditService.log",
                new_callable=AsyncMock,
            ):
                merge_task = asyncio.create_task(
                    CustomerMergeService.merge(
                        merge_db,
                        target_customer_id=ids["target_id"],
                        source_customer_id=ids["source_id"],
                        reason="MySQL flash claim wins lifecycle gate regression",
                    )
                )
                await asyncio.wait_for(merge_waiting.wait(), timeout=10)
                await claim_db.commit()
                await asyncio.wait_for(merge_task, timeout=20)
                await merge_db.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_merge_wait)
            if claim_db.in_transaction():
                await claim_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        claim = await verify.get(FlashSaleClaim, claim_id)
        source = await verify.get(Customer, ids["source_id"])
        target = await verify.get(Customer, ids["target_id"])
        stock = await verify.scalar(
            select(FlashSaleStock).where(
                FlashSaleStock.campaign_id == ids["campaign_id"],
                FlashSaleStock.store_id == ids["store_id"],
            )
        )
        assert claim is not None and claim.status == "CANCELLED"
        assert claim.status_reason == "CUSTOMER_MERGED"
        assert int(claim.customer_id) == ids["source_id"]
        assert source is not None and source.account_status == "DISABLED"
        assert target is not None and target.account_status == "ACTIVE"
        assert stock is not None and stock.remaining_quantity == 0
    await _cleanup_flash_claim_race(maker, ids)


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_missing_customer_write_gate_fails_closed_on_mysql(db_session):
    """A missing mutex row must not be silently recreated by a write request."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    suffix = uuid.uuid4().hex
    customer = Customer(
        customer_no=f"GMY-{suffix[:28]}",
        status=1,
        account_status="ACTIVE",
    )
    db_session.add(customer)
    await db_session.flush()
    customer_id = int(customer.id)
    gate = await db_session.get(CustomerWriteGate, customer_id)
    assert gate is not None
    await db_session.delete(gate)
    await db_session.flush()

    with pytest.raises(AppException) as exc:
        await lock_customer_write_gates(db_session, [customer_id])
    assert exc.value.code == 50021
    assert exc.value.status_code == 500


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_pos_bind_rejects_stale_old_owner_after_real_merge_commits(db_session):
    """A bind using both old/new gates must reject when merge migrates the old owner."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    async with maker() as setup:
        store = Store(store_no=f"PBO-{suffix[:18]}", name="POS bind old-owner merge race", status=1)
        source = Customer(customer_no=f"PBOS-{suffix[:25]}")
        target = Customer(customer_no=f"PBOT-{suffix[:25]}")
        setup.add_all([store, source, target])
        await setup.flush()
        order = Order(
            order_no=f"PBO-{suffix[:24]}",
            store_id=int(store.id),
            customer_id=int(source.id),
            status="PENDING_PAYMENT",
            payment_status="UNPAID",
            channel="POS",
            dining_type="TAKEAWAY",
            subtotal=Decimal("10.00"),
            discount_amount=Decimal("0.00"),
            coupon_discount=Decimal("0.00"),
            voucher_discount=Decimal("0.00"),
            packing_fee=Decimal("0.00"),
            total_amount=Decimal("10.00"),
        )
        setup.add(order)
        await setup.flush()
        ids = {
            "store_id": int(store.id),
            "source_id": int(source.id),
            "target_id": int(target.id),
            "order_id": int(order.id),
        }
        await setup.commit()

    merge_gate_acquired = asyncio.Event()
    allow_merge_to_continue = asyncio.Event()
    bind_gate_waiting = asyncio.Event()
    bind_gate_ids = []
    merge_connection = None

    from app.api import orders_pos
    import app.services.customer_merge_service as merge_module

    original_merge_lock = merge_module.lock_customer_write_gates
    original_bind_lock = orders_pos.lock_customer_write_gates

    async def pause_merge_after_gates(db, customer_ids):
        locked = await original_merge_lock(db, customer_ids)
        if db is merge_db:
            merge_gate_acquired.set()
            await asyncio.wait_for(allow_merge_to_continue.wait(), timeout=20)
        return locked

    async def observe_bind_gate_lock(db, customer_ids):
        bind_gate_ids.append(list(customer_ids))
        return await original_bind_lock(db, customer_ids)

    async with maker() as merge_db, maker() as bind_db:
        merge_connection = (await merge_db.connection()).sync_connection

        def observe_bind_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is not merge_connection
                and "CUSTOMER_WRITE_GATES" in normalized
                and "FOR UPDATE" in normalized
            ):
                bind_gate_waiting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_bind_wait)
        try:
            with (
                patch.object(merge_module, "lock_customer_write_gates", new=pause_merge_after_gates),
                patch.object(orders_pos, "lock_customer_write_gates", new=observe_bind_gate_lock),
                patch.object(orders_pos, "assert_order_store_access", lambda *_: None),
                patch.object(orders_pos, "_recompute_pos_amounts_for_customer", new_callable=AsyncMock),
                patch.object(orders_pos.AuditService, "log_staff", new_callable=AsyncMock),
                patch("app.services.customer_merge_service.AuditService.log", new_callable=AsyncMock),
            ):
                merge_task = asyncio.create_task(
                    CustomerMergeService.merge(
                        merge_db,
                        target_customer_id=ids["target_id"],
                        source_customer_id=ids["source_id"],
                        reason="MySQL POS bind old-owner lifecycle regression",
                    )
                )
                await asyncio.wait_for(merge_gate_acquired.wait(), timeout=10)

                bind_task = asyncio.create_task(
                    orders_pos.bind_pos_customer(
                        order_id=ids["order_id"],
                        data=type("BindData", (), {"customer_id": ids["target_id"]})(),
                        request=type("Request", (), {})(),
                        db=bind_db,
                        user=type("User", (), {"id": 1, "store_id": ids["store_id"]})(),
                    )
                )
                await asyncio.wait_for(bind_gate_waiting.wait(), timeout=10)
                allow_merge_to_continue.set()
                await asyncio.wait_for(merge_task, timeout=20)
                await merge_db.commit()
                with pytest.raises(AppException) as exc:
                    await asyncio.wait_for(bind_task, timeout=15)
                assert exc.value.status_code == 409
                assert exc.value.code == 40906
                assert bind_gate_ids == [[ids["source_id"], ids["target_id"]]]
        finally:
            allow_merge_to_continue.set()
            event.remove(engine.sync_engine, "before_cursor_execute", observe_bind_wait)
            if bind_db.in_transaction():
                await bind_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as verify:
        source = await verify.get(Customer, ids["source_id"])
        target = await verify.get(Customer, ids["target_id"])
        order = await verify.get(Order, ids["order_id"])
        assert source is not None and source.account_status == "DISABLED"
        assert target is not None and target.account_status == "ACTIVE"
        assert order is not None and order.customer_id == ids["target_id"]
        await verify.execute(delete(Order).where(Order.id == ids["order_id"]))
        await verify.execute(
            delete(Customer).where(Customer.id.in_([ids["source_id"], ids["target_id"]]))
        )
        await verify.execute(delete(Store).where(Store.id == ids["store_id"]))
        await verify.commit()



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_flash_sale_verify_commits_before_real_merge_preserves_used_entitlement(db_session):
    """Verification that owns the gate first completes; merge preserves its USED claim."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    ids = await _seed_flash_claim_race(maker, uuid.uuid4().hex)
    claim_id = None
    order_id = None

    async with maker() as claim_db:
        with (
            patch("app.services.flash_sale_service.MarketingService.is_enabled",
                  new=AsyncMock(return_value=True)),
            patch("app.services.flash_sale_service.MarketingService.is_store_participating",
                  new=AsyncMock(return_value=True)),
        ):
            claimed = await FlashSaleService.claim(
                claim_db,
                campaign_id=ids["campaign_id"],
                store_id=ids["store_id"],
                customer_id=ids["source_id"],
                client_request_id=f"fverify-{uuid.uuid4().hex[:24]}",
            )
        claim_id = int(claimed["claim_id"])
        await claim_db.commit()

    verify_gate_acquired = asyncio.Event()
    allow_verify_to_continue = asyncio.Event()
    merge_waiting = asyncio.Event()
    original_verify_lock = __import__(
        "app.services.flash_sale_service", fromlist=["lock_customer_write_gates"]
    ).lock_customer_write_gates

    async def pause_verify_after_gate(db, customer_ids):
        result = await original_verify_lock(db, customer_ids)
        if db is verify_db:
            verify_gate_acquired.set()
            await asyncio.wait_for(allow_verify_to_continue.wait(), timeout=20)
        return result

    async with maker() as verify_db, maker() as merge_db:
        verify_connection = (await verify_db.connection()).sync_connection

        def observe_merge_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is not verify_connection
                and "CUSTOMER_WRITE_GATES" in normalized
                and "FOR UPDATE" in normalized
            ):
                merge_waiting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_merge_wait)
        try:
            import app.services.flash_sale_service as flash_module

            with patch.object(flash_module, "lock_customer_write_gates", new=pause_verify_after_gate):
                verify_task = asyncio.create_task(
                    FlashSaleService.verify(
                        verify_db,
                        claim_no=claimed["claim_no"],
                        store_id=ids["store_id"],
                        operator_id=None,
                    )
                )
                await asyncio.wait_for(verify_gate_acquired.wait(), timeout=10)
                with patch(
                    "app.services.customer_merge_service.AuditService.log",
                    new_callable=AsyncMock,
                ):
                    merge_task = asyncio.create_task(
                        CustomerMergeService.merge(
                            merge_db,
                            target_customer_id=ids["target_id"],
                            source_customer_id=ids["source_id"],
                            reason="MySQL flash verify wins lifecycle gate regression",
                        )
                    )
                    await asyncio.wait_for(merge_waiting.wait(), timeout=10)
                    allow_verify_to_continue.set()
                    verified = await asyncio.wait_for(verify_task, timeout=20)
                    order_id = int(verified["order_id"])
                    await verify_db.commit()
                    await asyncio.wait_for(merge_task, timeout=20)
                    await merge_db.commit()
        finally:
            allow_verify_to_continue.set()
            event.remove(engine.sync_engine, "before_cursor_execute", observe_merge_wait)
            if verify_db.in_transaction():
                await verify_db.rollback()
            if merge_db.in_transaction():
                await merge_db.rollback()

    async with maker() as check:
        claim = await check.get(FlashSaleClaim, claim_id)
        order = await check.get(Order, order_id)
        source = await check.get(Customer, ids["source_id"])
        target = await check.get(Customer, ids["target_id"])
        assert claim is not None and claim.status == "USED"
        assert claim.customer_id == ids["source_id"]
        assert claim.order_id == order_id
        assert order is not None and order.customer_id == ids["target_id"]
        assert source is not None and source.account_status == "DISABLED"
        assert target is not None and target.status == 1
        replay = await FlashSaleService.verify(
            check,
            claim_no=claimed["claim_no"],
            store_id=ids["store_id"],
            operator_id=None,
        )
        assert replay["already_verified"] is True
        assert replay["order_id"] == order_id

    async with maker() as cleanup:
        await cleanup.execute(
            delete(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
        )
        await cleanup.execute(delete(Payment).where(Payment.order_id == order_id))
        await cleanup.execute(delete(Order).where(Order.id == order_id))
        await cleanup.commit()
    await _cleanup_flash_claim_race(maker, ids)


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_verified_phone_binding_rejects_identity_owner_changed_while_waiting_for_gates(db_session):
    """The identity current read must reject an owner change made after observation."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    phone = f"155{suffix[:8]}"
    async with maker() as setup:
        source = Customer(customer_no=f"VPS-{suffix[:27]}")
        observed_owner = Customer(customer_no=f"VPO-{suffix[:27]}")
        changed_owner = Customer(customer_no=f"VPN-{suffix[:27]}")
        setup.add_all([source, observed_owner, changed_owner])
        await setup.flush()
        source_id = int(source.id)
        observed_owner_id = int(observed_owner.id)
        changed_owner_id = int(changed_owner.id)
        setup.add(
            CustomerIdentity(
                customer_id=observed_owner_id,
                provider="PHONE",
                provider_subject=phone,
                verified=1,
            )
        )
        await setup.commit()

    gate_waiting = asyncio.Event()
    async with maker() as blocker_db, maker() as bind_db, maker() as identity_db:
        # Hold every gate the binding call will observe. This lets its initial
        # non-locking identity read complete, then pauses it before the gate.
        from app.services.customer_write_gate import lock_customer_write_gates

        await lock_customer_write_gates(
            blocker_db, [source_id, observed_owner_id]
        )
        source_for_binding = await bind_db.get(Customer, source_id)
        assert source_for_binding is not None
        bind_connection = (await bind_db.connection()).sync_connection

        def observe_gate_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is bind_connection
                and "CUSTOMER_WRITE_GATES" in normalized
                and "FOR UPDATE" in normalized
            ):
                gate_waiting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        try:
            bind_task = asyncio.create_task(
                CustomerService.bind_verified_phone(
                    bind_db,
                    source_for_binding,
                    phone,
                    verification_source="WECHAT",
                    confirm_merge=True,
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)

            # Simulate a legacy/out-of-band identity writer racing after the
            # observation. A compliant writer also takes gates; this mutation
            # deliberately exercises the defensive stale-owner check.
            await identity_db.execute(
                update(CustomerIdentity)
                .where(
                    CustomerIdentity.provider == "PHONE",
                    CustomerIdentity.provider_subject == phone,
                )
                .values(customer_id=changed_owner_id)
            )
            await identity_db.commit()
            await blocker_db.commit()

            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(bind_task, timeout=15)
            assert exc.value.status_code == 409
            assert exc.value.code == 40905
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            for session in (bind_db, blocker_db, identity_db):
                if session.in_transaction():
                    await session.rollback()

    async with maker() as cleanup:
        await cleanup.execute(
            delete(CustomerIdentity).where(
                CustomerIdentity.provider == "PHONE",
                CustomerIdentity.provider_subject == phone,
            )
        )
        await cleanup.execute(
            delete(Customer).where(
                Customer.id.in_([source_id, observed_owner_id, changed_owner_id])
            )
        )
        await cleanup.commit()



@pytest.mark.mysql
@pytest.mark.asyncio
async def test_verified_phone_binding_rejects_peer_added_while_waiting_for_gates(db_session):
    """The post-gate current read must reject a same-phone peer added after observation."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    phone = f"156{suffix[:8]}"
    async with maker() as setup:
        source = Customer(customer_no=f"VPSP-{suffix[:25]}")
        setup.add(source)
        await setup.flush()
        source_id = int(source.id)
        await setup.commit()

    gate_waiting = asyncio.Event()
    async with maker() as blocker_db, maker() as bind_db, maker() as peer_db:
        await lock_customer_write_gates(blocker_db, [source_id])
        source_for_binding = await bind_db.get(Customer, source_id)
        assert source_for_binding is not None
        bind_connection = (await bind_db.connection()).sync_connection

        def observe_gate_wait(conn, _cursor, statement, _parameters, _context, _many):
            normalized = statement.upper()
            if (
                conn is bind_connection
                and "CUSTOMER_WRITE_GATES" in normalized
                and "FOR UPDATE" in normalized
            ):
                gate_waiting.set()

        event.listen(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
        peer_id = None
        try:
            bind_task = asyncio.create_task(
                CustomerService.bind_verified_phone(
                    bind_db,
                    source_for_binding,
                    phone,
                    verification_source="WECHAT",
                    confirm_merge=True,
                )
            )
            await asyncio.wait_for(gate_waiting.wait(), timeout=10)

            # Simulate a pre-gate/legacy writer introducing a same-phone customer
            # after the binding call's initial peer snapshot.
            peer = Customer(
                customer_no=f"VPP-{suffix[:27]}",
                phone=phone,
                status=1,
                account_status="ACTIVE",
            )
            peer_db.add(peer)
            await peer_db.flush()
            peer_id = int(peer.id)
            await peer_db.commit()
            await blocker_db.commit()

            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(bind_task, timeout=15)
            assert exc.value.status_code == 409
            assert exc.value.code == 40907
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", observe_gate_wait)
            for session in (bind_db, blocker_db, peer_db):
                if session.in_transaction():
                    await session.rollback()

    async with maker() as cleanup:
        await cleanup.execute(
            delete(CustomerIdentity).where(
                CustomerIdentity.provider == "PHONE",
                CustomerIdentity.provider_subject == phone,
            )
        )
        customer_ids = [source_id]
        if peer_id is not None:
            customer_ids.append(peer_id)
        await cleanup.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
        await cleanup.commit()


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_verified_phone_binding_identity_insert_race_reads_committed_winner(db_session):
    """A real unique-key collision exposes its committed winner to the losing transaction."""
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    suffix = uuid.uuid4().hex
    phone = f"157{suffix[:8]}"
    async with maker() as setup:
        first = Customer(customer_no=f"VPI1-{suffix[:24]}")
        second = Customer(customer_no=f"VPI2-{suffix[:24]}")
        setup.add_all([first, second])
        await setup.flush()
        first_id, second_id = int(first.id), int(second.id)
        await setup.commit()

    loser_reached_insert = asyncio.Event()
    allow_loser_insert = asyncio.Event()
    async with maker() as winner_db, maker() as loser_db:
        # READ COMMITTED is used only to let the two-session unique-key race
        # pass the pre-insert locking lookup without an InnoDB gap-lock deadlock.
        # The loser still performs its initial non-locking reads before the
        # winner commits, then must recover the unique-key conflict correctly.
        await loser_db.connection(execution_options={"isolation_level": "READ COMMITTED"})
        winner_customer = await winner_db.get(Customer, first_id)
        loser_customer = await loser_db.get(Customer, second_id)
        assert winner_customer is not None and loser_customer is not None

        original_loser_flush = loser_db.flush

        async def pause_loser_identity_flush(*args, **kwargs):
            if any(isinstance(obj, CustomerIdentity) for obj in loser_db.new):
                loser_reached_insert.set()
                await asyncio.wait_for(allow_loser_insert.wait(), timeout=15)
            return await original_loser_flush(*args, **kwargs)

        loser_db.flush = pause_loser_identity_flush
        try:
            loser_task = asyncio.create_task(
                CustomerService.bind_verified_phone(
                    loser_db,
                    loser_customer,
                    phone,
                    verification_source="WECHAT",
                )
            )
            # The loser has observed no identity and reached the INSERT flush.
            # Let the winner commit the same unique key before allowing that
            # INSERT to proceed, deterministically producing IntegrityError.
            await asyncio.wait_for(loser_reached_insert.wait(), timeout=10)
            await CustomerService.bind_verified_phone(
                winner_db,
                winner_customer,
                phone,
                verification_source="WECHAT",
            )
            await winner_db.commit()
            allow_loser_insert.set()

            with pytest.raises(AppException) as exc:
                await asyncio.wait_for(loser_task, timeout=15)
            assert exc.value.status_code == 409
            assert exc.value.code == 40905
            assert exc.value.message == "手机号关联账号已变化，请重新查询后重试"
        finally:
            allow_loser_insert.set()
            loser_db.flush = original_loser_flush
            for session in (winner_db, loser_db):
                if session.in_transaction():
                    await session.rollback()

    async with maker() as verify:
        identity = await verify.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.provider == "PHONE",
                CustomerIdentity.provider_subject == phone,
                CustomerIdentity.verified == 1,
            )
        )
        assert identity is not None
        assert int(identity.customer_id) == first_id

    async with maker() as cleanup:
        await cleanup.execute(
            delete(CustomerIdentity).where(
                CustomerIdentity.provider == "PHONE",
                CustomerIdentity.provider_subject == phone,
            )
        )
        await cleanup.execute(
            delete(Customer).where(Customer.id.in_([first_id, second_id]))
        )
        await cleanup.commit()
