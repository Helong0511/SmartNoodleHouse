"""支付渠道 T+1 对账：导入账单 CSV 与本地 payments 比对，生成差异工单。"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import now_local
from app.models import Order, Payment, PaymentReconcileBatch, PaymentReconcileDiff, RechargeOrder


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        value = Decimal(str(v).strip().replace(",", ""))
        return value if value.is_finite() else None
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
        raise ValueError("渠道账单缺少 CSV 表头")
    # 兼容列名；缺少关键列时必须拒绝，不能把错误文件当作零交易账单。
    keys = {k.strip().lower(): k for k in reader.fieldnames if k}
    trade_key = keys.get("trade_no") or keys.get("transaction_id") or keys.get("渠道交易号")
    amt_key = keys.get("amount") or keys.get("应结订单金额") or keys.get("金额")
    pay_key = keys.get("payment_no") or keys.get("out_trade_no") or keys.get("商户订单号")
    if not trade_key or not amt_key:
        raise ValueError("渠道账单缺少交易号或金额列")
    for row_no, raw in enumerate(reader, start=2):
        # 允许文件末尾空行，但不允许非空数据行被静默跳过。
        if not any(str(v or "").strip() for v in raw.values()):
            continue
        trade_no = str(raw.get(trade_key) or "").strip()
        amount = _dec(raw.get(amt_key))
        if not trade_no:
            raise ValueError(f"渠道账单第 {row_no} 行缺少交易号")
        if amount is None:
            raise ValueError(f"渠道账单第 {row_no} 行金额无效或非有限数值")
        # CNY 金额最多两位小数；拒绝 NaN/Infinity、负数和精度异常，防止错误账单被当作有效数据。
        if amount < 0:
            raise ValueError(f"渠道账单金额不能为负数: {trade_no}")
        try:
            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError(f"渠道账单金额最多两位小数: {trade_no}")
        except InvalidOperation as exc:
            raise ValueError(f"渠道账单金额超出有效范围: {trade_no}") from exc
        # 交易号是渠道账单的唯一匹配键；重复行不能通过 dict 覆盖静默丢失，
        # 否则 channel_count 与差异数会失真，可能把重复扣款/账单异常当成正常。
        if trade_no in seen_trade_numbers:
            raise ValueError(f"渠道账单存在重复交易号: {trade_no}")
        seen_trade_numbers.add(trade_no)
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
        scope_store_id: int = 0,
    ) -> PaymentReconcileBatch:
        ch = channel.upper().strip()
        scope_id = int(scope_store_id or 0)
        if scope_id < 0:
            raise ValueError("scope_store_id 不能为负数")
        if ch not in ("WECHAT", "ALIPAY"):
            raise ValueError("channel 仅支持 WECHAT/ALIPAY")

        # 已存在批次时加行锁，串行化同渠道/日期的重跑；首次并发创建则由唯一键兜底。
        existing = (
            await db.execute(
                select(PaymentReconcileBatch)
                .where(
                    PaymentReconcileBatch.channel == ch,
                    PaymentReconcileBatch.bill_date == bill_date,
                    PaymentReconcileBatch.scope_store_id == scope_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is None:
            candidate = PaymentReconcileBatch(
                channel=ch,
                bill_date=bill_date,
                scope_store_id=scope_id,
                status="PENDING",
                created_by=created_by,
            )
            try:
                # SAVEPOINT 保护唯一键竞争，不回滚调用方事务中此前已完成的业务写入。
                async with db.begin_nested():
                    db.add(candidate)
                    await db.flush()
                existing = candidate
            except IntegrityError:
                # 另一并发导入先创建了相同批次；读取并锁定赢家，复用同一批次。
                existing = (
                    await db.execute(
                        select(PaymentReconcileBatch)
                        .where(
                            PaymentReconcileBatch.channel == ch,
                            PaymentReconcileBatch.bill_date == bill_date,
                            PaymentReconcileBatch.scope_store_id == scope_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise

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
            batch = existing
            batch.status = "PENDING"
            batch.error_message = None
            if batch.created_by is None and created_by is not None:
                batch.created_by = created_by
        batch.source_filename = source_filename
        await db.flush()

        try:
            channel_rows = parse_channel_bill_csv(ch, bill_csv)
            if scope_id > 0 and channel_rows:
                # 门店级账单必须保留商户支付单号，以便阻止把同一商户号的总部总账
                # 误导入单店批次。无法判断归属的行宁可拒绝，也不制造跨店 LONG 差异。
                if any(not row.get("local_payment_no") for row in channel_rows):
                    raise ValueError("门店级对账账单必须包含商户支付单号列（payment_no/out_trade_no）")
                payment_numbers = list(
                    dict.fromkeys(str(row["local_payment_no"]) for row in channel_rows)
                )
                # 账单可能包含其他门店的有效交易；检查已知商户单号的真实归属。
                # 未在本地找到的单号仍保留为 LONG 差异，供门店核查渠道侧孤儿交易。
                for offset in range(0, len(payment_numbers), 500):
                    chunk = payment_numbers[offset : offset + 500]
                    ownership_rows = (
                        await db.execute(
                            select(Payment.payment_no, Order.store_id, RechargeOrder.store_id)
                            .outerjoin(Order, Payment.order_id == Order.id)
                            .outerjoin(RechargeOrder, Payment.recharge_order_id == RechargeOrder.id)
                            .where(
                                Payment.provider == ch,
                                Payment.payment_no.in_(chunk),
                            )
                        )
                    ).all()
                    for payment_no, order_store_id, recharge_store_id in ownership_rows:
                        owner_store_id = order_store_id or recharge_store_id
                        if owner_store_id is None or int(owner_store_id) != scope_id:
                            raise ValueError(
                                f"门店账单包含其他门店或无法确认归属的支付单: {payment_no}"
                            )
            start = datetime.combine(bill_date, time.min).replace(tzinfo=now_local().tzinfo)
            end = start + timedelta(days=1)
            local_payments = list(
                (
                    await db.execute(
                        select(Payment)
                        .outerjoin(Order, Payment.order_id == Order.id)
                        .outerjoin(RechargeOrder, Payment.recharge_order_id == RechargeOrder.id)
                        .where(
                            and_(
                                Payment.provider == ch,
                                Payment.status.in_(("PAID", "PARTIAL_REFUND", "REFUNDED")),
                                # 对账日按渠道实际支付成功时间，而非本地创建支付单时间归属。
                                Payment.paid_at >= start,
                                Payment.paid_at < end,
                                *(
                                    [or_(Order.store_id == scope_id, RechargeOrder.store_id == scope_id)]
                                    if scope_id > 0
                                    else []
                                ),
                            )
                        )
                    )
                ).scalars().all()
            )

            by_trade: dict[str, Payment] = {}
            by_no: dict[str, Payment] = {}
            for p in local_payments:
                if p.provider_trade_no:
                    trade_key = str(p.provider_trade_no)
                    if trade_key in by_trade and by_trade[trade_key].id != p.id:
                        raise ValueError(f"本地支付记录存在重复渠道交易号: {trade_key}")
                    by_trade[trade_key] = p
                if p.payment_no:
                    payment_key = str(p.payment_no)
                    if payment_key in by_no and by_no[payment_key].id != p.id:
                        raise ValueError(f"本地支付记录存在重复支付单号: {payment_key}")
                    by_no[payment_key] = p

            channel_by_trade = {r["channel_trade_no"]: r for r in channel_rows}
            diffs: list[PaymentReconcileDiff] = []

            for trade_no, crow in channel_by_trade.items():
                local = by_trade.get(trade_no)
                if not local and crow.get("local_payment_no"):
                    candidate = by_no.get(str(crow["local_payment_no"]))
                    # 只有本地尚无渠道交易号时才允许用商户支付单号补匹配；
                    # 若本地已有另一个渠道交易号，必须同时暴露渠道 LONG + 本地 SHORT，
                    # 不可用商户单号掩盖交易号串单/账单错误。
                    if candidate and not candidate.provider_trade_no:
                        local = candidate
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
