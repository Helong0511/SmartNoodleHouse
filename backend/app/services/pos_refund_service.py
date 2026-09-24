"""收银退款申请：申请 / 审批 / 执行（支持组合分笔支付）。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
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
    async def approve_and_execute(
        cls,
        db: AsyncSession,
        *,
        user,
        request_id: int,
        remark: str | None = None,
    ) -> PosRefundRequest:
        """审批通过并执行退款。组合支付按「现金优先 → 扫码」分摊到多笔支付单。"""
        if not await _can_approve(db, user):
            raise AppException(40301, "仅店长或管理员可审批退款")
        row = (
            await db.execute(
                select(PosRefundRequest).where(PosRefundRequest.id == int(request_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if not row:
            raise AppException(40404, "退款申请不存在")
        assert_store_access(user, row.store_id)
        if row.status not in ("PENDING", "FAILED"):
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

        last_refund = None
        try:
            for payment, amt in alloc:
                last_refund = await PaymentService.refund(
                    db,
                    payment,
                    amt,
                    row.reason,
                    operator_id=user.id,
                )
                if last_refund.status == "SUCCESS":
                    executed = money(executed + amt)
                    row.executed_amount = executed
                    row.payment_id = payment.id
                    row.refund_id = last_refund.id
                    await db.flush()
                elif last_refund.status in ("PENDING", "PROCESSING", "UNKNOWN"):
                    # 渠道仅受理/处理中：申请单保持 PROCESSING，不得记入 executed 或标 SUCCESS
                    row.status = "PROCESSING"
                    row.payment_id = payment.id
                    row.refund_id = last_refund.id
                    row.fail_reason = None
                    await db.flush()
                    await db.refresh(row)
                    return row
                else:
                    row.status = "FAILED"
                    row.fail_reason = (
                        f"分摊执行中断（已退 ¥{executed}）："
                        f"{last_refund.fail_reason or last_refund.status}"
                    )[:255]
                    await db.flush()
                    raise AppException(50030, row.fail_reason)
        except AppException:
            if executed > 0 and money(row.amount) - executed > 0:
                row.status = "FAILED"
                if not row.fail_reason:
                    row.fail_reason = f"部分退款成功 ¥{executed}，剩余请重试"[:255]
                await db.flush()
            raise
        except Exception as e:
            row.status = "FAILED"
            row.fail_reason = (
                f"退款失败（已退 ¥{executed}）: {e}" if executed > 0 else f"退款执行失败: {e}"
            )[:255]
            await db.flush()
            raise AppException(50030, row.fail_reason) from e

        if executed + money(0) >= money(row.amount):
            row.status = "SUCCESS"
            row.fail_reason = None
            summary = cls._alloc_summary(alloc)
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
        from datetime import datetime

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
                q = q.where(PosRefundRequest.created_at >= datetime.fromisoformat(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                end = datetime.fromisoformat(date_to)
                if len(date_to) <= 10:
                    from datetime import timedelta

                    end = end + timedelta(days=1)
                q = q.where(PosRefundRequest.created_at < end)
            except ValueError:
                pass

        total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        rows = (
            await db.execute(
                q.order_by(PosRefundRequest.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()
        items = [await cls.payload(db, r) for r in rows]
        return {"total": int(total), "page": page, "page_size": page_size, "items": items}

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
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "review_remark": row.review_remark,
            "refund_id": row.refund_id,
            "fail_reason": row.fail_reason,
            "dining_type": order.dining_type if order else None,
            "table_no": table.table_no if table else None,
            "table_name": table.table_name if table else None,
            "created_at": (
                row.created_at.isoformat()
                if getattr(row, "created_at", None) is not None
                else None
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
