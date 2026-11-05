"""支付对账与订单冷库查询 API。"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, serialize_dt, success
from app.models import ArchivedOrderSnapshot, PaymentReconcileBatch, PaymentReconcileDiff
from app.schemas.pagination import clamp_page
from app.services.order_archive_service import OrderArchiveService
from app.services.payment_reconcile_service import PaymentReconcileService
from app.services.store_scope import assert_store_access, effective_store_id

router = APIRouter(prefix="/reconcile", tags=["reconcile"])

# 对账 CSV 不是普通文本输入：限制读取上限，避免授权用户上传超大文件耗尽内存。
MAX_RECONCILE_BILL_BYTES = 10 * 1024 * 1024


@router.post("/payments/import", dependencies=[require_permission("reconcile:manage")])
async def import_payment_bill(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    channel: Annotated[str, Form()],
    bill_date: Annotated[date, Form()],
    file: UploadFile = File(...),
):
    raw = await file.read(MAX_RECONCILE_BILL_BYTES + 1)
    if len(raw) > MAX_RECONCILE_BILL_BYTES:
        raise AppException(41312, "渠道账单文件不能超过 10 MiB", status_code=413)
    if not raw:
        raise AppException(40012, "渠道账单文件为空", status_code=400)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError as exc:
            raise AppException(40012, "渠道账单编码不受支持，请使用 UTF-8 或 GBK", status_code=400) from exc
    # 文件名仅作为审计元数据保存；剥离路径分隔符、控制字符并限制长度。
    source_filename = str(file.filename or "channel-bill.csv").replace("\\\\", "/").split("/")[-1]
    source_filename = "".join(ch for ch in source_filename if ch.isprintable())[:255] or "channel-bill.csv"
    try:
        batch = await PaymentReconcileService.run_batch(
            db,
            channel=channel,
            bill_date=bill_date,
            bill_csv=text,
            created_by=user.id,
            source_filename=source_filename,
        )
        await db.commit()
    except ValueError as e:
        raise AppException(40012, str(e), status_code=400) from e
    return success(
        {
            "id": batch.id,
            "channel": batch.channel,
            "bill_date": batch.bill_date.isoformat(),
            "status": batch.status,
            "local_count": batch.local_count,
            "channel_count": batch.channel_count,
            "mismatch_count": batch.mismatch_count,
        }
    )


@router.get("/payments/batches", dependencies=[require_permission("reconcile:read")])
async def list_batches(
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    limit: int | None = Query(None, ge=1, le=200),
):
    # limit 兼容旧前端；有 page 时以分页为准
    if limit is not None and page == 1 and page_size == 20:
        page_size = min(limit, 100)
    page, page_size = clamp_page(page, page_size)
    q = select(PaymentReconcileBatch)
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(PaymentReconcileBatch.bill_date.desc(), PaymentReconcileBatch.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    return success(
        {
            "items": [
                {
                    "id": b.id,
                    "channel": b.channel,
                    "bill_date": b.bill_date.isoformat(),
                    "status": b.status,
                    "local_count": b.local_count,
                    "channel_count": b.channel_count,
                    "mismatch_count": b.mismatch_count,
                    "created_at": serialize_dt(b.created_at),
                }
                for b in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(int(total), page_size),
        }
    )


@router.get("/payments/batches/{batch_id}/diffs", dependencies=[require_permission("reconcile:read")])
async def list_diffs(batch_id: int, db: DbSession, user: CurrentUser):
    rows = list(
        (
            await db.execute(
                select(PaymentReconcileDiff)
                .where(PaymentReconcileDiff.batch_id == batch_id)
                .order_by(PaymentReconcileDiff.id.asc())
            )
        ).scalars().all()
    )
    return success(
        {
            "items": [
                {
                    "id": d.id,
                    "diff_type": d.diff_type,
                    "local_payment_no": d.local_payment_no,
                    "channel_trade_no": d.channel_trade_no,
                    "local_amount": str(d.local_amount) if d.local_amount is not None else None,
                    "channel_amount": str(d.channel_amount) if d.channel_amount is not None else None,
                    "status": d.status,
                    "detail": d.detail,
                }
                for d in rows
            ]
        }
    )


class ArchiveRunBody(BaseModel):
    older_than_days: int = Field(365, ge=30, le=3650)
    limit: int = Field(200, ge=1, le=1000)
    dry_run: bool = False


@router.post("/orders/archive", dependencies=[require_permission("archive:manage")])
async def run_order_archive(body: ArchiveRunBody, db: DbSession, user: CurrentUser):
    result = await OrderArchiveService.archive_older_than(
        db,
        older_than_days=body.older_than_days,
        limit=body.limit,
        dry_run=body.dry_run,
    )
    if not body.dry_run:
        await db.commit()
    return success(result)


@router.get("/orders/archived", dependencies=[require_permission("order:read")])
async def list_archived_orders(
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    limit: int | None = Query(None, ge=1, le=200),
):
    sid = effective_store_id(user, store_id)
    if sid is not None:
        assert_store_access(user, sid)
    if limit is not None and page == 1 and page_size == 20:
        page_size = min(limit, 100)
    page, page_size = clamp_page(page, page_size)
    q = select(ArchivedOrderSnapshot)
    if sid is not None:
        q = q.where(ArchivedOrderSnapshot.store_id == sid)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        q = q.where(ArchivedOrderSnapshot.order_no.like(kw))
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(
                    ArchivedOrderSnapshot.original_created_at.desc(),
                    ArchivedOrderSnapshot.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    return success(
        {
            "items": [
                {
                    "id": r.id,
                    "order_id": r.order_id,
                    "order_no": r.order_no,
                    "store_id": r.store_id,
                    "status": r.status,
                    "payment_status": r.payment_status,
                    "original_created_at": serialize_dt(r.original_created_at),
                    "archived_at": serialize_dt(r.archived_at),
                }
                for r in rows
            ],
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(int(total), page_size),
        }
    )


@router.get("/orders/archived/{order_no}", dependencies=[require_permission("order:read")])
async def get_archived_order(order_no: str, db: DbSession, user: CurrentUser):
    row = (
        await db.execute(
            select(ArchivedOrderSnapshot).where(ArchivedOrderSnapshot.order_no == order_no)
        )
    ).scalar_one_or_none()
    if not row:
        raise AppException(40400, "归档订单不存在", status_code=404)
    assert_store_access(user, row.store_id)
    return success(
        {
            "id": row.id,
            "order_no": row.order_no,
            "payload": row.payload,
            "archived_at": serialize_dt(row.archived_at),
        }
    )
