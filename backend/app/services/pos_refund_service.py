"""收银退款申请：申请 / 审批 / 执行。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import now_local
from app.models import DiningTable, Order, Payment, PosRefundRequest, User
from app.services.payment_service import PaymentService, money
from app.services.store_scope import assert_order_store_access, assert_store_access


def _can_approve(user) -> bool:
    return user.role.code in ("SUPER_ADMIN", "STORE_ADMIN")


class PosRefundService:
    @classmethod
    async def _paid_payment_for_order(cls, db: AsyncSession, order_id: int) -> Payment:
        payment = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.order_id == int(order_id),
                    Payment.status.in_(("PAID", "PARTIAL_REFUND")),
                )
                .order_by(Payment.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not payment:
            raise AppException(40401, "未找到可退款的支付记录")
        return payment

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

        payment = await cls._paid_payment_for_order(db, order.id)
        # 收银员只能对自己操作收款的单申请（店长可代申请）
        if user.role.code == "CASHIER":
            if payment.operator_id != user.id:
                raise AppException(40301, "只能申请本人收款的订单退款")

        refundable = await PaymentService.refundable_amount(db, payment)
        if refundable <= 0:
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

        row = PosRefundRequest(
            store_id=order.store_id,
            order_id=order.id,
            payment_id=payment.id,
            amount=amt,
            reason=reason_s[:255],
            status="PENDING",
            requested_by=user.id,
        )
        db.add(row)
        await db.flush()
        return row

    @classmethod
    async def cancel_request(cls, db: AsyncSession, *, user, request_id: int) -> PosRefundRequest:
        row = await db.get(PosRefundRequest, int(request_id))
        if not row:
            raise AppException(40404, "退款申请不存在")
        if row.requested_by != user.id and not _can_approve(user):
            raise AppException(40301, "无权撤销该申请")
        if row.status != "PENDING":
            raise AppException(40014, "仅待审批申请可撤销")
        row.status = "CANCELLED"
        await db.flush()
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
        if not _can_approve(user):
            raise AppException(40301, "仅店长或管理员可审批退款")
        row = await db.get(PosRefundRequest, int(request_id))
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
        """审批通过并立即执行退款（微信/支付宝走原路，现金本地入账）。"""
        if not _can_approve(user):
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

        payment = await db.get(Payment, row.payment_id)
        if not payment:
            raise AppException(40401, "支付记录不存在")

        row.reviewed_by = user.id
        row.reviewed_at = now_local()
        if remark and remark.strip():
            row.review_remark = remark.strip()[:500]

        try:
            refund_row = await PaymentService.refund(
                db,
                payment,
                row.amount,
                row.reason,
                operator_id=user.id,
            )
        except AppException as e:
            row.status = "FAILED"
            row.fail_reason = e.message[:255]
            await db.flush()
            raise
        except Exception as e:
            row.status = "FAILED"
            row.fail_reason = str(e)[:255]
            await db.flush()
            raise AppException(50030, f"退款执行失败: {e}") from e

        row.refund_id = refund_row.id
        if refund_row.status == "SUCCESS":
            row.status = "SUCCESS"
            row.fail_reason = None
        elif refund_row.status in ("PENDING", "PROCESSING"):
            # 平台受理中：申请单先标 SUCCESS 表示已审批执行，实际以 payment_refunds 为准
            row.status = "SUCCESS"
            row.fail_reason = None
        else:
            row.status = "FAILED"
            row.fail_reason = (refund_row.fail_reason or refund_row.status)[:255]
        await db.flush()
        return row

    @classmethod
    async def list_requests(
        cls,
        db: AsyncSession,
        *,
        user,
        status: str | None = None,
        store_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        from app.services.store_scope import require_bound_store

        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        q = select(PosRefundRequest)
        if user.role.code == "CASHIER":
            q = q.where(PosRefundRequest.requested_by == user.id)
        else:
            effective = require_bound_store(user, store_id)
            if effective is not None:
                q = q.where(PosRefundRequest.store_id == int(effective))
        if status:
            q = q.where(PosRefundRequest.status == status.strip().upper())

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
        return {
            "id": row.id,
            "store_id": row.store_id,
            "order_id": row.order_id,
            "order_no": order.order_no if order else None,
            "payment_id": row.payment_id,
            "payment_no": payment.payment_no if payment else None,
            "payment_method": (payment.provider if payment else None) or (order.payment_method if order else None),
            "amount": str(row.amount),
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
            "created_at": row.created_at.isoformat() if row.created_at else None,
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
