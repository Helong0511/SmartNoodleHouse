"""客户分群：条件树 estimate / materialize。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import Customer, CustomerSegment, CustomerSegmentMember

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500
SUPPORTED_FIELDS = frozenset(
    {
        "member_status",
        "account_status",
        "status",
        "registered_store_id",
        "points",
        "balance_total",
        "days_since_order",
        "days_since_member",
        "has_order",
    }
)
SUPPORTED_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"})


class SegmentService:
    @staticmethod
    def _balance_expr():
        return Customer.balance_principal + Customer.balance_gift

    @classmethod
    def _cmp(cls, col, op: str, value: Any):
        if op == "eq":
            return col == value
        if op == "ne":
            return col != value
        if op == "gt":
            return col > value
        if op == "gte":
            return col >= value
        if op == "lt":
            return col < value
        if op == "lte":
            return col <= value
        if op == "in":
            vals = list(value) if isinstance(value, (list, tuple, set)) else [value]
            return col.in_(vals)
        if op == "not_in":
            vals = list(value) if isinstance(value, (list, tuple, set)) else [value]
            return ~col.in_(vals)
        raise AppException(40001, f"不支持的运算符: {op}")

    @classmethod
    def _condition_to_clause(cls, cond: dict, *, now: datetime):
        if not isinstance(cond, dict):
            raise AppException(40001, "分群条件须为对象")
        if "all" in cond:
            parts = [cls._condition_to_clause(c, now=now) for c in (cond.get("all") or [])]
            return and_(*parts) if parts else True
        if "any" in cond:
            parts = [cls._condition_to_clause(c, now=now) for c in (cond.get("any") or [])]
            return or_(*parts) if parts else True

        field = str(cond.get("field") or "").strip()
        op = str(cond.get("op") or "eq").strip().lower()
        value = cond.get("value")
        if field not in SUPPORTED_FIELDS:
            raise AppException(40001, f"不支持的分群字段: {field}")
        if op not in SUPPORTED_OPS:
            raise AppException(40001, f"不支持的运算符: {op}")

        if field == "member_status":
            return cls._cmp(Customer.member_status, op, value)
        if field == "account_status":
            return cls._cmp(Customer.account_status, op, value)
        if field == "status":
            return cls._cmp(Customer.status, op, int(value))
        if field == "registered_store_id":
            return cls._cmp(Customer.registered_store_id, op, int(value) if value is not None else None)
        if field == "points":
            return cls._cmp(Customer.points, op, int(value))
        if field == "balance_total":
            return cls._cmp(cls._balance_expr(), op, Decimal(str(value)))
        if field == "has_order":
            want = bool(value)
            if want:
                return Customer.last_order_at.isnot(None)
            return Customer.last_order_at.is_(None)
        if field == "days_since_order":
            days = int(value)
            boundary = now - timedelta(days=days)
            # days_since_order >= N  ⟺ last_order_at <= now-N（含无消费）
            if op in ("gte", "gt"):
                cmp_op = "lte" if op == "gte" else "lt"
                return or_(Customer.last_order_at.is_(None), cls._cmp(Customer.last_order_at, cmp_op, boundary))
            if op in ("lte", "lt"):
                cmp_op = "gte" if op == "lte" else "gt"
                return and_(Customer.last_order_at.isnot(None), cls._cmp(Customer.last_order_at, cmp_op, boundary))
            if op == "eq":
                start = now - timedelta(days=days + 1)
                return and_(Customer.last_order_at.isnot(None), Customer.last_order_at <= boundary, Customer.last_order_at > start)
            raise AppException(40001, "days_since_order 仅支持 gt/gte/lt/lte/eq")
        if field == "days_since_member":
            days = int(value)
            boundary = now - timedelta(days=days)
            if op in ("gte", "gt"):
                cmp_op = "lte" if op == "gte" else "lt"
                return and_(
                    Customer.member_activated_at.isnot(None),
                    cls._cmp(Customer.member_activated_at, cmp_op, boundary),
                )
            if op in ("lte", "lt"):
                cmp_op = "gte" if op == "lte" else "gt"
                return and_(
                    Customer.member_activated_at.isnot(None),
                    cls._cmp(Customer.member_activated_at, cmp_op, boundary),
                )
            raise AppException(40001, "days_since_member 仅支持 gt/gte/lt/lte")
        raise AppException(40001, f"未实现字段: {field}")

    @classmethod
    def build_filter(cls, rules_json: dict | list | None, *, now: datetime | None = None, store_id: int | None = None):
        now = now or naive_local_now()
        clauses = [Customer.status == 1, Customer.account_status == "ACTIVE"]
        if store_id is not None:
            clauses.append(
                or_(Customer.registered_store_id == int(store_id), Customer.registered_store_id.is_(None))
            )
        if rules_json:
            if isinstance(rules_json, list):
                rules_json = {"all": rules_json}
            clauses.append(cls._condition_to_clause(rules_json, now=now))
        return and_(*clauses)

    @classmethod
    async def estimate(
        cls,
        db: AsyncSession,
        *,
        rules_json: dict | list | None,
        store_id: int | None = None,
        sample_limit: int = 20,
    ) -> dict:
        filt = cls.build_filter(rules_json, store_id=store_id)
        total = int(await db.scalar(select(func.count()).select_from(Customer).where(filt)) or 0)
        rows = list(
            (
                await db.execute(
                    select(Customer)
                    .where(filt)
                    .order_by(Customer.id.desc())
                    .limit(max(0, min(int(sample_limit), 50)))
                )
            )
            .scalars()
            .all()
        )
        samples = [
            {
                "id": int(c.id),
                "customer_no": c.customer_no,
                "display_name": (c.display_name or c.nickname or "")[:20] or None,
                "phone_masked": cls._mask_phone(c.phone),
                "member_status": c.member_status,
                "balance_total": str((c.balance_principal or 0) + (c.balance_gift or 0)),
                "last_order_at": c.last_order_at.isoformat(sep=" ", timespec="seconds")
                if c.last_order_at
                else None,
            }
            for c in rows
        ]
        return {"count": total, "samples": samples}

    @staticmethod
    def _mask_phone(phone: str | None) -> str | None:
        if not phone:
            return None
        p = str(phone).strip()
        if len(p) < 7:
            return "***"
        return f"{p[:3]}****{p[-4:]}"

    @classmethod
    async def get_segment(cls, db: AsyncSession, segment_id: int) -> CustomerSegment:
        row = await db.get(CustomerSegment, int(segment_id))
        if not row:
            raise AppException(40401, "分群不存在")
        return row

    @classmethod
    async def list_segments(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        q = select(CustomerSegment)
        if store_id is not None:
            q = q.where(or_(CustomerSegment.store_id == store_id, CustomerSegment.store_id.is_(None)))
        total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
        rows = list(
            (
                await db.execute(
                    q.order_by(CustomerSegment.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return [cls._payload(r) for r in rows], total

    @classmethod
    async def upsert(
        cls,
        db: AsyncSession,
        *,
        segment_id: int | None = None,
        store_id: int | None,
        code: str,
        name: str,
        segment_type: str = "DYNAMIC",
        rules_json: dict | list | None = None,
        status: int = 1,
    ) -> CustomerSegment:
        code = (code or "").strip()
        name = (name or "").strip()
        if not code or not name:
            raise AppException(40001, "分群 code/name 必填")
        st = (segment_type or "DYNAMIC").upper()
        if st not in ("STATIC", "DYNAMIC"):
            raise AppException(40001, "segment_type 须为 STATIC/DYNAMIC")
        if rules_json is not None:
            # 校验可编译
            cls.build_filter(rules_json, store_id=store_id)

        if segment_id:
            row = await cls.get_segment(db, segment_id)
            if store_id is not None and row.store_id not in (None, store_id):
                raise AppException(40301, "无权修改该分群")
            row.code = code
            row.name = name
            row.segment_type = st
            row.rules_json = rules_json
            row.status = int(status)
        else:
            exists = await db.scalar(
                select(CustomerSegment.id).where(
                    CustomerSegment.store_id == store_id,
                    CustomerSegment.code == code,
                )
            )
            if exists:
                raise AppException(40002, "分群 code 已存在")
            row = CustomerSegment(
                store_id=store_id,
                code=code,
                name=name,
                segment_type=st,
                rules_json=rules_json,
                status=int(status),
            )
            db.add(row)
        await db.flush()
        return row

    @classmethod
    async def materialize(
        cls,
        db: AsyncSession,
        segment_id: int,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> dict:
        seg = await cls.get_segment(db, segment_id)
        if int(seg.status) != 1:
            raise AppException(40001, "分群已停用")
        filt = cls.build_filter(seg.rules_json, store_id=seg.store_id)
        await db.execute(delete(CustomerSegmentMember).where(CustomerSegmentMember.segment_id == seg.id))
        inserted = 0
        last_id = 0
        while True:
            rows = list(
                (
                    await db.execute(
                        select(Customer.id)
                        .where(filt, Customer.id > last_id)
                        .order_by(Customer.id)
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            for cid in rows:
                db.add(CustomerSegmentMember(segment_id=int(seg.id), customer_id=int(cid)))
                inserted += 1
            last_id = int(rows[-1])
            await db.flush()
            if len(rows) < batch_size:
                break
        seg.estimated_count = inserted
        seg.materialized_at = naive_local_now()
        await db.flush()
        return {"segment_id": int(seg.id), "count": inserted}

    @classmethod
    async def iter_member_ids(
        cls,
        db: AsyncSession,
        segment_id: int,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        prefer_snapshot: bool = True,
    ):
        """分批产出顾客 id。STATIC 或已物化优先用快照；否则动态扫描。"""
        seg = await cls.get_segment(db, segment_id)
        use_snap = prefer_snapshot and (
            (seg.segment_type or "").upper() == "STATIC" or seg.materialized_at is not None
        )
        if use_snap:
            last_id = 0
            while True:
                rows = list(
                    (
                        await db.execute(
                            select(CustomerSegmentMember.customer_id)
                            .where(
                                CustomerSegmentMember.segment_id == seg.id,
                                CustomerSegmentMember.customer_id > last_id,
                            )
                            .order_by(CustomerSegmentMember.customer_id)
                            .limit(batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    return
                yield [int(x) for x in rows]
                last_id = int(rows[-1])
                if len(rows) < batch_size:
                    return
        filt = cls.build_filter(seg.rules_json, store_id=seg.store_id)
        last_id = 0
        while True:
            rows = list(
                (
                    await db.execute(
                        select(Customer.id)
                        .where(filt, Customer.id > last_id)
                        .order_by(Customer.id)
                        .limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return
            yield [int(x) for x in rows]
            last_id = int(rows[-1])
            if len(rows) < batch_size:
                return

    @staticmethod
    def _payload(row: CustomerSegment) -> dict:
        return {
            "id": int(row.id),
            "store_id": int(row.store_id) if row.store_id else None,
            "code": row.code,
            "name": row.name,
            "segment_type": row.segment_type,
            "rules_json": row.rules_json,
            "status": int(row.status),
            "estimated_count": row.estimated_count,
            "materialized_at": row.materialized_at.isoformat(sep=" ", timespec="seconds")
            if row.materialized_at
            else None,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds")
            if row.created_at
            else None,
            "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds")
            if row.updated_at
            else None,
        }
