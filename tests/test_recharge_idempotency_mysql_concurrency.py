"""Real MySQL race regression for recharge idempotency-key uniqueness."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, RechargeOrder, Store


@pytest.mark.mysql
@pytest.mark.asyncio
async def test_concurrent_recharge_idempotency_insert_isolated_by_savepoint(db_session):
    """The losing request sees the winner through a locking/current read.

    Two independent MySQL sessions both observe a missing idempotency key.
    The first inserts but does not commit; the second attempts the same unique
    key and blocks. Once the first commits, the second gets IntegrityError
    inside a SAVEPOINT, then SELECT FOR UPDATE sees the committed winner while
    keeping its outer transaction usable.
    """
    if db_session.get_bind().dialect.name != "mysql":
        pytest.skip("requires MySQL 8 / InnoDB")

    engine = db_session.bind
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    idem = f"recharge-{suffix[:32]}"

    async with maker() as setup:
        store = Store(store_no=f"RI-{suffix[:20]}", name="Recharge idempotency race")
        customer = Customer(customer_no=f"RC-{suffix[:30]}", points=0)
        setup.add_all([store, customer])
        await setup.flush()
        store_id, customer_id = store.id, customer.id
        await setup.commit()

    async with maker() as winner, maker() as loser:
        # Both transactions establish an ordinary missing-key read first.
        assert await winner.scalar(
            select(RechargeOrder.id).where(RechargeOrder.idempotency_key == idem)
        ) is None
        assert await loser.scalar(
            select(RechargeOrder.id).where(RechargeOrder.idempotency_key == idem)
        ) is None

        winner.add(
            RechargeOrder(
                recharge_no=f"RWIN-{suffix[:28]}",
                customer_id=customer_id,
                store_id=store_id,
                pay_amount="10.00",
                credit_principal="10.00",
                credit_gift="0.00",
                status="PENDING",
                channel="WECHAT",
                idempotency_key=idem,
            )
        )
        await winner.flush()

        async def losing_insert():
            with pytest.raises(IntegrityError):
                async with loser.begin_nested():
                    loser.add(RechargeOrder(
                        recharge_no=f"RLOS-{suffix[:28]}", customer_id=customer_id,
                        store_id=store_id, pay_amount="10.00", credit_principal="10.00",
                        credit_gift="0.00", status="PENDING", channel="WECHAT",
                        idempotency_key=idem,
                    ))
                    await loser.flush()

        task = asyncio.create_task(losing_insert())
        wait_sql = text("""
            SELECT COUNT(*) FROM performance_schema.data_lock_waits AS w
            JOIN performance_schema.data_locks AS req
              ON req.ENGINE = w.ENGINE
             AND req.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
            WHERE req.OBJECT_SCHEMA = DATABASE()
              AND req.OBJECT_NAME = 'recharge_orders'
        """)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        observed_wait = False
        try:
            async with engine.connect() as observer:
                while loop.time() < deadline:
                    waiting = int((await observer.execute(wait_sql)).scalar_one())
                    if waiting > 0:
                        observed_wait = True
                        break
                    if task.done():
                        await task
                        break
                    await asyncio.sleep(0.05)
            assert observed_wait, "InnoDB data_lock_waits did not show competing recharge INSERT"
            assert not task.done(), "competing recharge INSERT completed before winner commit"
            await winner.commit()
            await asyncio.wait_for(task, timeout=15)
            # A normal RR snapshot could miss the newly committed winner.
            # The production recovery path uses a locking/current read.
            winner_row = (
                await loser.execute(
                    select(RechargeOrder)
                    .where(RechargeOrder.idempotency_key == idem)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            assert winner_row.status == "PENDING"
            assert winner_row.pay_amount == 10
            # The outer transaction remains usable after the nested rollback.
            assert await loser.scalar(select(Store.id).where(Store.id == store_id)) == store_id
            await loser.commit()
        finally:
            if task is not None and not task.done():
                await winner.rollback()
                try:
                    await asyncio.wait_for(task, timeout=3)
                except Exception:
                    task.cancel()
            if winner.in_transaction():
                await winner.rollback()
            if loser.in_transaction():
                await loser.rollback()

    async with maker() as cleanup:
        await cleanup.execute(
            RechargeOrder.__table__.delete().where(RechargeOrder.idempotency_key == idem)
        )
        await cleanup.execute(Customer.__table__.delete().where(Customer.id == customer_id))
        await cleanup.execute(Store.__table__.delete().where(Store.id == store_id))
        await cleanup.commit()
