"""支付渠道 T+1 对账：导入账单 CSV 与本地 payments 比对，生成差异工单。"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import Payment, PaymentReconcileBatch, PaymentReconcileDiff


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def parse_channel_bill_csv(channel: str, text: str) -> list[dict[str, Any]]:
    """解析简化账单：trade_no,amount[,payment_no]。

    微信/支付宝官方账单列很多；此处接受精简 CSV 以便本地与 CI 可测。
    生产可先用脚本裁剪官方账单再导入。
    """
    rows: list[dict[str, Any]] = []
    seen_trade_numbers: set[str] = set()
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        return rows
    # 兼容列名
    for raw in reader:
        keys = {k.strip().lower(): k for k in raw.keys() if k}
        trade_key = keys.get("trade_no") or keys.get("transaction_id") or keys.get("渠道交易号")
        amt_key = keys.get("amount") or keys.get("应结订单金额") or keys.get("金额")
        pay_key = keys.get("payment_no") or keys.get("out_trade_no") or keys.get("商户订单号")
        if not trade_key or not amt_key:
            continue
        trade_no = str(raw.get(trade_key) or "").strip()
        amount = _dec(raw.get(amt_key))
        if not trade_no or amount is None:
            continue
        # 交易号是渠道账单的唯一匹配键；重复行不能通过 dict 覆盖静默丢失，
        # 否则 channel_count 与差异数会失真，可能把重复扣款/账单异常当成正常。
        if trade_no in seen_trade_numbers:
            raise ValueError(f"渠道账单存在重复交易号: {trade_no}")
        seen_trade_numbers.add(trade_no)
        if amount < 0:
            raise ValueError(f"渠道账单金额不能为负数: {trade_no}")
        rows.append(
            {
                "channel": channel.upper(),
                "channel_trade_no": trade_no,
                "channel_amount": amount,
                "local_payment_no": str(raw.get(pay_key) or "").strip() or None,
            }
        )
    return rows


class PaymentReconcileService:
    @staticmethod
    async def run_batch(
        db: AsyncSession,
        *,
        channel: str,
        bill_date: date,
        bill_csv: str,
        created_by: int | None = None,
        source_filename: str | None = None,
    ) -> PaymentReconcileBatch:
        ch = channel.upper().strip()
        if ch not in ("WECHAT", "ALIPAY"):
            raise ValueError("channel 仅支持 WECHAT/ALIPAY")

        existing = (
            await db.execute(
                select(PaymentReconcileBatch).where(
                    PaymentReconcileBatch.channel == ch,
                    PaymentReconcileBatch.bill_date == bill_date,
                )
            )
        ).scalar_one_or_none()
        if existing and existing.status == "DONE":
            # 重跑：清旧差异
            old = (
                await db.execute(
                    select(PaymentReconcileDiff).where(PaymentReconcileDiff.batch_id == existing.id)
                )
            ).scalars().all()
            for d in old:
                await db.delete(d)
            batch = existing
            batch.status = "PENDING"
            batch.error_message = None
        else:
            batch = existing or PaymentReconcileBatch(
                channel=ch,
                bill_date=bill_date,
                status="PENDING",
                created_by=created_by,
            )
            if existing is None:
                db.add(batch)
        batch.source_filename = source_filename
        await db.flush()

        try:
            channel_rows = parse_channel_bill_csv(ch, bill_csv)
            start = datetime.combine(bill_date, time.min).replace(tzinfo=now_local().tzinfo)
            end = start + timedelta(days=1)
            local_payments = list(
                (
                    await db.execute(
                        select(Payment).where(
                            and_(
                                Payment.provider == ch,
                                Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                                # 对账日按渠道实际支付成功时间，而非本地创建支付单时间归属。
                                Payment.paid_at >= start,
                                Payment.paid_at < end,
                            )
                        )
                    )
                ).scalars().all()
            )

            by_trade: dict[str, Payment] = {}
            by_no: dict[str, Payment] = {}
            for p in local_payments:
                if p.provider_trade_no:
                    by_trade[str(p.provider_trade_no)] = p
                if p.payment_no:
                    by_no[str(p.payment_no)] = p

            channel_by_trade = {r["channel_trade_no"]: r for r in channel_rows}
            diffs: list[PaymentReconcileDiff] = []

            for trade_no, crow in channel_by_trade.items():
                local = by_trade.get(trade_no)
                if not local and crow.get("local_payment_no"):
                    local = by_no.get(str(crow["local_payment_no"]))
                if not local:
                    diffs.append(
                        PaymentReconcileDiff(
                            batch_id=batch.id,
                            diff_type="LONG",
                            channel_trade_no=trade_no,
                            channel_amount=crow["channel_amount"],
                            detail={"hint": "渠道有本地无"},
                        )
                    )
                    continue
                local_amt = Decimal(str(local.amount or 0))
                if local_amt != crow["channel_amount"]:
                    diffs.append(
                        PaymentReconcileDiff(
                            batch_id=batch.id,
                            diff_type="AMOUNT_MISMATCH",
                            local_payment_no=local.payment_no,
                            channel_trade_no=trade_no,
                            local_amount=local_amt,
                            channel_amount=crow["channel_amount"],
                        )
                    )

            for p in local_payments:
                trade = str(p.provider_trade_no or "")
                if trade and trade not in channel_by_trade:
                    diffs.append(
                        PaymentReconcileDiff(
                            batch_id=batch.id,
                            diff_type="SHORT",
                            local_payment_no=p.payment_no,
                            channel_trade_no=trade or None,
                            local_amount=Decimal(str(p.amount or 0)),
                            detail={"hint": "本地有渠道无"},
                        )
                    )

            for d in diffs:
                db.add(d)

            batch.local_count = len(local_payments)
            batch.channel_count = len(channel_rows)
            batch.mismatch_count = len(diffs)
            batch.status = "DONE"
            await db.flush()
            return batch
        except Exception as e:
            batch.status = "FAILED"
            batch.error_message = str(e)[:480]
            await db.flush()
            raise
