from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import PaymentReconcileBatch
from app.services.payment_reconcile_service import PaymentReconcileService, parse_channel_bill_csv


def test_channel_bill_parser_keeps_trade_amount_and_payment_number():
    rows = parse_channel_bill_csv(
        "WECHAT",
        "trade_no,amount,payment_no\nWX-001,12.30,PAY-001\n",
    )
    assert len(rows) == 1
    assert rows[0]["channel"] == "WECHAT"
    assert rows[0]["channel_trade_no"] == "WX-001"
    assert rows[0]["channel_amount"] == Decimal("12.30")
    assert rows[0]["local_payment_no"] == "PAY-001"


def test_channel_bill_parser_rejects_duplicate_trade_numbers_instead_of_overwriting():
    with pytest.raises(ValueError, match="重复交易号"):
        parse_channel_bill_csv(
            "ALIPAY",
            "trade_no,amount\nALI-001,12.30\nALI-001,12.30\n",
        )


def test_channel_bill_parser_rejects_negative_amounts():
    with pytest.raises(ValueError, match="不能为负数"):
        parse_channel_bill_csv(
            "WECHAT",
            "trade_no,amount\nWX-NEG,-1.00\n",
        )

@pytest.mark.parametrize(
    "bill, message",
    [
        ("wrong,columns\nWX-1,1.00\n", "缺少交易号或金额列"),
        ("trade_no,amount\n,1.00\n", "缺少交易号"),
        ("trade_no,amount\nWX-1,NaN\n", "金额无效"),
        ("trade_no,amount\nWX-1,Infinity\n", "金额无效"),
        ("trade_no,amount\nWX-1,1.001\n", "最多两位小数"),
    ],
)
def test_channel_bill_parser_rejects_malformed_or_non_finite_amount_rows(bill, message):
    with pytest.raises(ValueError, match=message):
        parse_channel_bill_csv("WECHAT", bill)


def test_channel_bill_parser_rejects_missing_header():
    with pytest.raises(ValueError, match="缺少 CSV 表头"):
        parse_channel_bill_csv("WECHAT", "")


def test_channel_bill_parser_allows_empty_bill_with_valid_headers():
    assert parse_channel_bill_csv("WECHAT", "trade_no,amount,payment_no\n") == []

@pytest.mark.asyncio
async def test_reconcile_batch_rerun_reuses_same_batch_and_replaces_result(db_session):
    """同渠道同账期重复导入复用批次，不能创建第二条批次记录。"""
    bill_date = date(2026, 10, 31)
    csv_bill = "trade_no,amount,payment_no\n"

    first = await PaymentReconcileService.run_batch(
        db_session,
        channel="WECHAT",
        bill_date=bill_date,
        bill_csv=csv_bill,
        created_by=7,
        source_filename="wechat-20261031.csv",
    )
    first_id = first.id
    await db_session.commit()

    second = await PaymentReconcileService.run_batch(
        db_session,
        channel="wechat",
        bill_date=bill_date,
        bill_csv=csv_bill,
        created_by=8,
        source_filename="wechat-retry.csv",
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(PaymentReconcileBatch).where(
                PaymentReconcileBatch.channel == "WECHAT",
                PaymentReconcileBatch.bill_date == bill_date,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert second.id == first_id
    assert second.status == "DONE"
    assert second.source_filename == "wechat-retry.csv"



@pytest.mark.asyncio
async def test_reconcile_batches_are_unique_per_store_scope(db_session):
    """同一渠道/日期允许总部与各门店分别对账，但同作用域只能有一个批次。"""
    bill_date = date(2026, 11, 5)
    bill = "trade_no,amount,payment_no\n"

    hq = await PaymentReconcileService.run_batch(
        db_session,
        channel="WECHAT",
        bill_date=bill_date,
        bill_csv=bill,
        scope_store_id=0,
    )
    store_a = await PaymentReconcileService.run_batch(
        db_session,
        channel="WECHAT",
        bill_date=bill_date,
        bill_csv=bill,
        scope_store_id=101,
    )
    store_b = await PaymentReconcileService.run_batch(
        db_session,
        channel="WECHAT",
        bill_date=bill_date,
        bill_csv=bill,
        scope_store_id=202,
    )
    await db_session.commit()

    assert hq.scope_store_id == 0
    assert store_a.scope_store_id == 101
    assert store_b.scope_store_id == 202
    assert len({hq.id, store_a.id, store_b.id}) == 3

    retry = await PaymentReconcileService.run_batch(
        db_session,
        channel="wechat",
        bill_date=bill_date,
        bill_csv=bill,
        scope_store_id=101,
    )
    await db_session.commit()
    assert retry.id == store_a.id
