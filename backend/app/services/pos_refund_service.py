"""收银退款申请：申请 / 审批 / 执行（支持组合分笔支付）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.orm_safe import orm_attr_loaded
from app.core.response import ensure_local_aware, now_local, serialize_dt
from app.models import DiningTable, Order, Payment, PosRefundRequest, User
from app.services.payment_service import PaymentService, money
from app.services.store_scope import assert_order_store_access, assert_store_access


async def _can_approve(db: AsyncSession, user) -> bool:
    """店长、超管，或被授予支付退款权限的角色可审批。仍受门店隔离约束。"""
    if user.role.code in ("SUPER_ADMIN", "STORE_ADMIN"):
        return True
    from app.api.deps import get_user_permissions

    perms = await get_user_permissions(db, user.role_id)
    return "payment:refund" in perms


class PosRefundService:
    @classmethod
    async def _list_paid_payments(cls, db: AsyncSession, order_id: int) -> list[Payment]:
        return list(
            (
                await db.execute(
                    select(Payment)
                    .where(
                        Payment.order_id == int(order_id),
                        Payment.status.in_(("PAID", "PARTIAL_REFUND")),
                    )
                    .order_by(Payment.id.asc())
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def _refundable_pairs(
        cls, db: AsyncSession, order_id: int
    ) -> tuple[Decimal, list[tuple[Payment, Decimal]]]:
        """整单可退合计 + 各支付单可退余额。"""
        pairs: list[tuple[Payment, Decimal]] = []
        total = money(0)
        for p in await cls._list_paid_payments(db, order_id):
            rem = await PaymentService.refundable_amount(db, p)
            if rem > 0:
                pairs.append((p, rem))
                total = money(total + rem)
        return total, pairs

    @classmethod
    def _allocate(
        cls, amount: Decimal, pairs: list[tuple[Payment, Decimal]]
    ) -> list[tuple[Payment, Decimal]]:
        """分摊退款：现金优先（店内直接退），再按支付单从新到旧。

        例：支付宝 12 + 现金 9，退 10 → 现金 9 + 支付宝 1。
        """
        need = money(amount)
        if need <= 0:
            raise AppException(40051, "退款金额非法")
        ordered = sorted(
            pairs,
            key=lambda x: (0 if (x[0].provider or "").upper() == "CASH" else 1, -int(x[0].id)),
        )
        out: list[tuple[Payment, Decimal]] = []
        left = need
        for p, rem in ordered:
            if left <= 0:
                break
            take = money(min(left, rem))
            if take > 0:
                out.append((p, take))
                left = money(left - take)
        if left > 0:
            raise AppException(40052, f"退款金额超过可退余额 ¥{money(need - left)}")
        return out

    @classmethod
    def _alloc_summary(cls, alloc: list[tuple[Payment, Decimal]]) -> str:
        parts = []
        for p, amt in alloc:
            prov = (p.provider or "").upper()
            if prov == "CASH":
                name = "现金"
            elif prov == "WECHAT":
                name = "微信"
            elif prov == "ALIPAY":
                name = "支付宝"
            else:
                name = prov or "支付"
            parts.append(f"{name}¥{amt}")
        return " + ".join(parts)

    @classmethod
    async def create_request(
        cls,
        db: AsyncSession,
        *,
        user,
        order_id: int,
        amount: Decimal | None,
        reason: str,
    ) -> PosRefundRequest:
        order = await db.get(Order, int(order_id))
        if not order:
            raise AppException(40402, "订单不存在")
        assert_order_store_access(user, order)
        if (order.channel or "").upper() != "POS":
            raise AppException(40008, "仅支持收银渠道订单申请退款")
        if order.payment_status not in ("PAID", "PARTIAL_REFUND") and order.status not in (
            "PAID",
            "CONFIRMED",
            "PREPARING",
            "COOKING",
            "READY",
            "COMPLETED",
            "PARTIAL_REFUND",
        ):
            raise AppException(40050, "当前订单状态不可退款")

        all_paid = await cls._list_paid_payments(db, order.id)
        if not all_paid:
            raise AppException(40401, "未找到可退款的支付记录")

        # 收银员：须本人经手过本单至少一笔收款（组合支付同班员）
        if user.role.code == "CASHIER":
            if not any(p.operator_id == user.id for p in all_paid):
                raise AppException(40301, "只能申请本人收款的订单退款")

        refundable, pairs = await cls._refundable_pairs(db, order.id)
        if refundable <= 0 or not pairs:
            raise AppException(40053, "可退余额为 0")
        amt = money(amount) if amount is not None else refundable
        if amt <= 0:
            raise AppException(40051, "退款金额非法")
        if amt > refundable:
            raise AppException(40052, f"退款金额超过可退余额 ¥{refundable}")

        pending = await db.scalar(
            select(func.count()).select_from(PosRefundRequest).where(
                PosRefundRequest.order_id == order.id,
                PosRefundRequest.status == "PENDING",
            )
        ) or 0
        if int(pending) > 0:
            raise AppException(40054, "该订单已有待审批的退款申请")

        reason_s = (reason or "").strip()
        if not reason_s:
            raise AppException(40008, "请填写退款原因")

        alloc = cls._allocate(amt, pairs)
        row = PosRefundRequest(
            store_id=order.store_id,
            order_id=order.id,
            payment_id=alloc[0][0].id,  # 主支付单（优先现金）；执行时仍按分摊多笔退
            amount=amt,
            executed_amount=money(0),
            reason=reason_s[:255],
            status="PENDING",
            requested_by=user.id,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    @classmethod
    async def create_customer_request(
        cls,
        db: AsyncSession,
        *,
        customer_id: int,
        order_id: int,
        reason: str | None,
    ) -> PosRefundRequest:
        """顾客自助退款：仅线上点餐渠道、已支付且后厨未开始制作的整单全额退款申请。

        幂等：该订单已有待审批申请时直接返回原申请。资金执行复用现有审批流
        （店长/超管审批 → 按支付分摊原路退回）。
        """
        order = await db.get(Order, int(order_id))
        if not order:
            raise AppException(40404, "订单不存在")
        if order.customer_id != int(customer_id):
            # 他人订单不暴露存在性
            raise AppException(40404, "订单不存在")
        if (order.channel or "").upper() not in ("WECHAT", "ALIPAY", "H5"):
            raise AppException(40055, "仅线上点餐订单支持自助退款")
        if order.payment_status != "PAID":
            raise AppException(40056, "仅已支付订单可申请退款")
        if order.status != "CONFIRMED" or order.kitchen_claimed_by is not None:
            raise AppException(40057, "订单已开始制作，请联系商家协商退款")
        pending = (
            (
                await db.execute(
                    select(PosRefundRequest)
                    .where(
                        PosRefundRequest.order_id == order.id,
                        PosRefundRequest.status == "PENDING",
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if pending is not None:
            return pending
        all_paid = await cls._list_paid_payments(db, order.id)
        if not all_paid:
            raise AppException(40401, "未找到可退款的支付记录")
        refundable, pairs = await cls._refundable_pairs(db, order.id)
        if refundable <= 0 or not pairs:
            raise AppException(40053, "可退余额为 0")
        amt = money(refundable)
        if amt <= 0:
            raise AppException(40051, "退款金额非法")
        alloc = cls._allocate(amt, pairs)
        reason_s = f"[顾客自助] {(reason or '未开始制作，申请全额退款').strip()}"[:255]
        row = PosRefundRequest(
            store_id=order.store_id,
            order_id=order.id,
            payment_id=alloc[0][0].id,  # 主支付单；执行时仍按分摊多笔退
            amount=amt,
            executed_amount=money(0),
            reason=reason_s,
            status="PENDING",
            requested_by=None,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    @classmethod
    async def cancel_request(cls, db: AsyncSession, *, user, request_id: int) -> PosRefundRequest:
        # FOR UPDATE：与 approve_and_execute（可能正调用外部退款渠道）互斥，
        # 避免审批提交后被无锁的撤销覆写成 CANCELLED（钱已退、单据丢失）。
        row = (
            await db.execute(
                select(PosRefundRequest).where(PosRefundRequest.id == int(request_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40404, "退款申请不存在")
        from app.services.store_scope import assert_store_access

        assert_store_access(user, row.store_id)
        if row.requested_by != user.id and not await _can_approve(db, user):
            raise AppException(40301, "无权撤销该申请")
        if row.status != "PENDING":
            raise AppException(40014, "仅待审批申请可撤销")
        row.status = "CANCELLED"
        await db.flush()
        await db.refresh(row)
        return row

    @classmethod
    async def reject_request(
        cls,
        db: AsyncSession,
        *,
        user,
        request_id: int,
        remark: str | None,
    ) -> PosRefundRequest:
        if not await _can_approve(db, user):
            raise AppException(40301, "仅店长或管理员可审批退款")
        # FOR UPDATE：同 cancel_request，拒绝与审批互斥，防止覆写退款结果
        row = (
            await db.execute(
                select(PosRefundRequest).where(PosRefundRequest.id == int(request_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40404, "退款申请不存在")
        assert_store_access(user, row.store_id)
        if row.status != "PENDING":
            raise AppException(40014, "仅待审批申请可拒绝")
        row.status = "REJECTED"
        row.reviewed_by = user.id
        row.reviewed_at = now_local()
        row.review_remark = (remark or "").strip() or None
        await db.flush()
        await db.refresh(row)
        return row

    @classmethod
    async def _lock_request(cls, db: AsyncSession, request_id: int) -> PosRefundRequest | None:
        return (
            await db.execute(
                select(PosRefundRequest)
                .where(PosRefundRequest.id == int(request_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    @classmethod
    async def approve_and_execute(
        cls,
        db: AsyncSession,
        *,
        user,
        request_id: int,
        remark: str | None = None,
    ) -> PosRefundRequest:
        """审批通过并执行退款。组合支付按「现金优先 → 扫码」分摊到多笔支付单。

        先短锁认领 PROCESSING 并 commit，再调 PaymentService.refund（其内部会再 commit 后调渠道）。
        避免申请单仍为 PENDING 时被并发撤销，也避免在 PosRefundRequest 行锁内等待微信/支付宝。
        """
        if not await _can_approve(db, user):
            raise AppException(40301, "仅店长或管理员可审批退款")
        row = await cls._lock_request(db, request_id)
        if not row:
            raise AppException(40404, "退款申请不存在")
        assert_store_access(user, row.store_id)
        # PROCESSING 且已绑定渠道退款单时，先检查原单状态；未终结的退款绝不重新发起。
        if row.status == "PROCESSING" and row.refund_id:
            from app.models import PaymentRefund

            linked_refund = await db.get(PaymentRefund, int(row.refund_id))
            if linked_refund and linked_refund.status in ("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL"):
                await db.refresh(row)
                return row
        # PROCESSING：仅在上一笔已明确结束后，才允许继续处理剩余金额。
        # PENDING/FAILED：首次执行或明确失败后的重试。
        if row.status not in ("PENDING", "FAILED", "PROCESSING"):
            raise AppException(40014, "当前状态不可执行退款")

        row.reviewed_by = user.id
        row.reviewed_at = now_local()
        if remark and remark.strip():
            row.review_remark = remark.strip()[:500]

        executed = money(getattr(row, "executed_amount", None) or 0)
        need = money(row.amount) - executed
        if need <= 0:
            row.status = "SUCCESS"
            row.fail_reason = None
            await db.flush()
            await db.refresh(row)
            return row

        refundable, pairs = await cls._refundable_pairs(db, row.order_id)
        if need > refundable:
            row.status = "FAILED"
            row.fail_reason = f"可退余额不足：待退 ¥{need}，可退 ¥{refundable}"[:255]
            await db.flush()
            await db.refresh(row)
            raise AppException(40052, row.fail_reason)

        try:
            alloc = cls._allocate(need, pairs)
        except AppException as e:
            row.status = "FAILED"
            row.fail_reason = e.message[:255]
            await db.flush()
            await db.refresh(row)
            raise

        reason = row.reason
        amount_total = money(row.amount)
        alloc_snapshot = [(int(p.id), money(amt), (p.provider or "").upper()) for p, amt in alloc]
        # 认领后立刻释放申请单行锁（PaymentService.refund 也会 commit）
        row.status = "PROCESSING"
        row.fail_reason = None
        await db.flush()
        await db.commit()

        last_refund = None
        try:
            for payment_id, amt, _prov in alloc_snapshot:
                payment = await db.get(Payment, payment_id)
                if not payment:
                    raise AppException(40401, "支付记录不存在")
                last_refund = await PaymentService.refund(
                    db,
                    payment,
                    amt,
                    reason,
                    operator_id=user.id,
                    pos_refund_request_id=request_id,
                )
                row = await cls._lock_request(db, request_id)
                if not row:
                    raise AppException(40404, "退款申请不存在")
                if last_refund.status == "SUCCESS":
                    # 新流程在 PaymentService.refund 内已先绑定 refund_id，
                    # _finalize_refund 会在同一事务内累计 executed_amount。
                    # 仅对旧数据（尚未建立关联）保留兼容性补记，防止重复累计。
                    if row.refund_id != last_refund.id:
                        executed = money(executed + amt)
                        row.executed_amount = executed
                        row.payment_id = payment_id
                        row.refund_id = last_refund.id
                    else:
                        executed = money(row.executed_amount or 0)
                    row.status = "SUCCESS" if executed >= amount_total else "PROCESSING"
                    await db.flush()
                    await db.commit()
                elif last_refund.status in ("PENDING", "PROCESSING", "UNKNOWN", "ABNORMAL"):
                    # 渠道仅受理/处理中/异常待处理：保持 PROCESSING，不得记入 executed 或标 SUCCESS
                    row.status = "PROCESSING"
                    row.payment_id = payment_id
                    row.refund_id = last_refund.id
                    row.fail_reason = None
                    await db.flush()
                    await db.commit()
                    await db.refresh(row)
                    return row
                else:
                    row.status = "FAILED"
                    row.fail_reason = (
                        f"分摊执行中断（已退 ¥{executed}）："
                        f"{last_refund.fail_reason or last_refund.status}"
                    )[:255]
                    await db.flush()
                    await db.commit()
                    raise AppException(50030, row.fail_reason)
        except AppException as e:
            row = await cls._lock_request(db, request_id)
            if row and row.status not in ("SUCCESS", "REJECTED", "CANCELLED"):
                row.status = "FAILED"
                if executed > 0 and amount_total - executed > 0:
                    if not row.fail_reason:
                        row.fail_reason = f"部分退款成功 ¥{executed}，剩余请重试"[:255]
                elif not row.fail_reason:
                    row.fail_reason = (getattr(e, "message", None) or str(e))[:255]
                await db.flush()
                await db.commit()
            raise
        except Exception as e:
            row = await cls._lock_request(db, request_id)
            if row and row.status not in ("SUCCESS", "REJECTED", "CANCELLED"):
                row.status = "FAILED"
                row.fail_reason = (
                    f"退款失败（已退 ¥{executed}）: {e}" if executed > 0 else f"退款执行失败: {e}"
                )[:255]
                await db.flush()
                await db.commit()
                raise AppException(50030, row.fail_reason) from e
            raise AppException(50030, f"退款执行失败: {e}") from e

        row = await cls._lock_request(db, request_id)
        if not row:
            raise AppException(40404, "退款申请不存在")
        if executed + money(0) >= amount_total:
            row.status = "SUCCESS"
            row.fail_reason = None
            from types import SimpleNamespace

            summary = cls._alloc_summary(
                [(SimpleNamespace(provider=prov), amt) for _pid, amt, prov in alloc_snapshot]
            )
            if summary and not (row.review_remark or "").strip():
                row.review_remark = f"分摊：{summary}"[:500]
        else:
            row.status = "FAILED"
            row.fail_reason = f"部分退款成功 ¥{executed}，剩余请重试"[:255]
        await db.flush()
        await db.refresh(row)
        return row

    @classmethod
    async def list_requests(
        cls,
        db: AsyncSession,
        *,
        user,
        status: str | None = None,
        store_id: int | None = None,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        from sqlalchemy import or_

        from app.services.store_scope import require_bound_store

        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        q = select(PosRefundRequest)
        if not await _can_approve(db, user):
            q = q.where(PosRefundRequest.requested_by == user.id)
        else:
            effective = require_bound_store(user, store_id)
            if effective is not None:
                q = q.where(PosRefundRequest.store_id == int(effective))
        if status:
            q = q.where(PosRefundRequest.status == status.strip().upper())
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            q = (
                q.join(Order, Order.id == PosRefundRequest.order_id)
                .join(Payment, Payment.id == PosRefundRequest.payment_id)
                .where(or_(Order.order_no.like(kw), Payment.payment_no.like(kw)))
            )
        if date_from:
            try:
                dt = ensure_local_aware(datetime.fromisoformat(date_from))
                q = q.where(PosRefundRequest.created_at >= dt)
            except ValueError:
                raise AppException(40001, f"日期格式非法: {date_from}")
        if date_to:
            try:
                end = datetime.fromisoformat(date_to)
                if len(date_to) <= 10:
                    end = end + timedelta(days=1)
                end = ensure_local_aware(end)
                q = q.where(PosRefundRequest.created_at < end)
            except ValueError:
                raise AppException(40001, f"日期格式非法: {date_to}")

        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        rows = (
            await db.execute(
                q.order_by(PosRefundRequest.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()
        # 批量预加载关联实体，消除 payload 中逐行 db.get 的 N+1
        order_ids = {r.order_id for r in rows if r.order_id}
        payment_ids = {r.payment_id for r in rows if r.payment_id}
        user_ids = {r.requested_by for r in rows if r.requested_by}
        user_ids |= {r.reviewed_by for r in rows if r.reviewed_by}
        if order_ids:
            loaded_orders = list((await db.execute(select(Order).where(Order.id.in_(order_ids)))).scalars())
            table_ids = {o.table_id for o in loaded_orders if o.table_id}
            if table_ids:
                await db.execute(select(DiningTable).where(DiningTable.id.in_(table_ids)))
        if payment_ids:
            await db.execute(select(Payment).where(Payment.id.in_(payment_ids)))
        if user_ids:
            await db.execute(select(User).where(User.id.in_(user_ids)))
        items = [await cls.payload(db, r) for r in rows]
        from app.core.response import calc_pages

        return {
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(int(total), page_size),
            "items": items,
        }

    @classmethod
    async def payload(cls, db: AsyncSession, row: PosRefundRequest) -> dict:
        order = await db.get(Order, row.order_id)
        payment = await db.get(Payment, row.payment_id)
        requester = await db.get(User, row.requested_by)
        reviewer = await db.get(User, row.reviewed_by) if row.reviewed_by else None
        table = await db.get(DiningTable, order.table_id) if order and order.table_id else None
        refundable, pairs = await cls._refundable_pairs(db, row.order_id) if order else (money(0), [])
        tenders = [
            {
                "payment_no": p.payment_no,
                "provider": p.provider,
                "amount": str(money(p.amount)),
                "refundable": str(rem),
            }
            for p, rem in pairs
        ]
        executed = money(getattr(row, "executed_amount", None) or 0)
        return {
            "id": row.id,
            "store_id": row.store_id,
            "order_id": row.order_id,
            "order_no": order.order_no if order else None,
            "payment_id": row.payment_id,
            "payment_no": payment.payment_no if payment else None,
            "payment_method": (order.payment_method if order else None)
            or (payment.provider if payment else None),
            "amount": str(row.amount),
            "executed_amount": str(executed),
            "remaining_to_execute": str(money(money(row.amount) - executed)),
            "order_refundable": str(refundable),
            "tenders": tenders,
            "reason": row.reason,
            "status": row.status,
            "requested_by": row.requested_by,
            "requester_name": (requester.display_name or requester.username) if requester else None,
            "reviewed_by": row.reviewed_by,
            "reviewer_name": (reviewer.display_name or reviewer.username) if reviewer else None,
            "reviewed_at": serialize_dt(row.reviewed_at),
            "review_remark": row.review_remark,
            "refund_id": row.refund_id,
            "fail_reason": row.fail_reason,
            "dining_type": order.dining_type if order else None,
            "table_no": table.table_no if table else None,
            "table_name": table.table_name if table else None,
            "created_at": (
                serialize_dt(row.created_at) if orm_attr_loaded(row, "created_at") else None
            ),
        }

    @classmethod
    async def latest_by_orders(cls, db: AsyncSession, order_ids: list[int]) -> dict[int, PosRefundRequest]:
        if not order_ids:
            return {}
        rows = (
            await db.execute(
                select(PosRefundRequest)
                .where(PosRefundRequest.order_id.in_(order_ids))
                .order_by(PosRefundRequest.id.desc())
            )
        ).scalars().all()
        out: dict[int, PosRefundRequest] = {}
        for r in rows:
            if r.order_id not in out:
                out[r.order_id] = r
        return out

    @classmethod
    async def order_refund_summary(cls, db: AsyncSession, order_id: int) -> dict:
        refundable, pairs = await cls._refundable_pairs(db, order_id)
        return {
            "refundable_amount": str(refundable),
            "tenders": [
                {
                    "payment_no": p.payment_no,
                    "provider": p.provider,
                    "amount": str(money(p.amount)),
                    "refundable": str(rem),
                }
                for p, rem in pairs
            ],
        }
