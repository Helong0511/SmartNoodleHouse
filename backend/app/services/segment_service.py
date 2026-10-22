"""客户分群：条件树 estimate / materialize。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.response import naive_local_now
from app.models import (
    Customer,
    CustomerLifecycle,
    CustomerSegment,
    CustomerSegmentMember,
    CustomerSocialIdentity,
)

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
        # 来自 customer_lifecycle 夜批快照
        "lifecycle_code",
        "rfm_segment",
        "silence_days",
        # 社会贡献公益身份（customer_social_identities，仅 ACTIVE 且在有效期内）
        "social_identity_code",
        # 今日是否生日（公历/农历；SQL 仅预筛有生日，精确判定见 post-filter）
        "is_birthday_today",
        # 今日是否入会周年（仅无生日；锚点 member_activated_at/created_at；精确判定见 post-filter）
        "is_member_anniversary_today",
    }
)
SUPPORTED_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in"})
_DAY_BOOL_FIELDS = frozenset({"is_birthday_today", "is_member_anniversary_today"})


class SegmentService:
    @classmethod
    def rules_contain_field(cls, rules_json: dict | list | None, field: str) -> bool:
        """规则树是否包含指定 field（不看 op/value）。"""
        if not rules_json or not field:
            return False
        if isinstance(rules_json, list):
            rules_json = {"all": rules_json}
        want = str(field).strip()

        def walk(node) -> bool:
            if not isinstance(node, dict):
                return False
            if "all" in node or "any" in node:
                return any(walk(c) for c in (node.get("all") or node.get("any") or []))
            return str(node.get("field") or "").strip() == want

        return walk(rules_json)

    @classmethod
    def _rules_need_day_bool(
        cls, rules_json: dict | list | None, field: str
    ) -> bool | None:
        """若规则含指定日布尔字段：True=只要是，False=只要否；无则 None。"""
        if field not in _DAY_BOOL_FIELDS:
            raise AppException(40001, f"不支持的日判定字段: {field}")
        if not rules_json:
            return None
        if isinstance(rules_json, list):
            rules_json = {"all": rules_json}

        def walk(node) -> bool | None:
            if not isinstance(node, dict):
                return None
            if "all" in node or "any" in node:
                found = None
                for c in node.get("all") or node.get("any") or []:
                    r = walk(c)
                    if r is not None:
                        found = r
                return found
            if str(node.get("field") or "").strip() != field:
                return None
            op = str(node.get("op") or "eq").strip().lower()
            raw = node.get("value")
            want = raw in (True, 1, "1", "true", "True", "yes", "YES")
            if op == "eq":
                return want
            if op == "ne":
                return not want
            raise AppException(40001, f"{field} 仅支持 eq/ne")

        return walk(rules_json)

    @classmethod
    def _rules_need_birthday_today(cls, rules_json: dict | list | None) -> bool | None:
        return cls._rules_need_day_bool(rules_json, "is_birthday_today")

    @classmethod
    def _rules_need_member_anniversary_today(
        cls, rules_json: dict | list | None
    ) -> bool | None:
        return cls._rules_need_day_bool(rules_json, "is_member_anniversary_today")

    @classmethod
    def _day_post_filter_wants(
        cls, rules_json: dict | list | None
    ) -> tuple[bool | None, bool | None]:
        """(want_birthday, want_anniversary)；任一非 None 则需拉候选后置过滤。"""
        return (
            cls._rules_need_birthday_today(rules_json),
            cls._rules_need_member_anniversary_today(rules_json),
        )

    @classmethod
    def _is_birthday_today_customer(cls, c: Customer) -> bool:
        from app.utils.birthday import is_birthday_today

        return bool(
            is_birthday_today(
                getattr(c, "birthday", None),
                getattr(c, "birthday_calendar", None),
            )
        )

    @classmethod
    def _is_member_anniversary_today_customer(cls, c: Customer) -> bool:
        from app.utils.birthday import is_member_anniversary_today

        return bool(
            is_member_anniversary_today(
                getattr(c, "birthday", None),
                getattr(c, "member_activated_at", None),
                created_at=getattr(c, "created_at", None),
            )
        )

    @classmethod
    def _customer_matches_day_post_filters(
        cls,
        c: Customer,
        *,
        want_bday: bool | None,
        want_anniv: bool | None,
    ) -> bool:
        if want_bday is not None and cls._is_birthday_today_customer(c) is not want_bday:
            return False
        if (
            want_anniv is not None
            and cls._is_member_anniversary_today_customer(c) is not want_anniv
        ):
            return False
        return True
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
        if field == "lifecycle_code":
            return Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    cls._cmp(CustomerLifecycle.lifecycle_code, op, value)
                )
            )
        if field == "rfm_segment":
            return Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    cls._cmp(CustomerLifecycle.rfm_segment, op, value)
                )
            )
        if field == "silence_days":
            return Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    cls._cmp(CustomerLifecycle.silence_days, op, int(value))
                )
            )
        if field == "social_identity_code":
            if op not in ("eq", "ne", "in", "not_in"):
                raise AppException(40001, "social_identity_code 仅支持 eq/ne/in/not_in")
            now_cmp = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
            window_ok = and_(
                CustomerSocialIdentity.status == "ACTIVE",
                or_(
                    CustomerSocialIdentity.valid_from.is_(None),
                    CustomerSocialIdentity.valid_from <= now_cmp,
                ),
                or_(
                    CustomerSocialIdentity.valid_to.is_(None),
                    CustomerSocialIdentity.valid_to >= now_cmp,
                ),
            )
            # ne/not_in：排除「持有该 ACTIVE 身份」的顾客
            if op in ("ne", "not_in"):
                pos_op = "eq" if op == "ne" else "in"
                hit = (
                    select(CustomerSocialIdentity.customer_id)
                    .where(
                        window_ok,
                        cls._cmp(CustomerSocialIdentity.identity_code, pos_op, value),
                    )
                    .distinct()
                )
                return ~Customer.id.in_(hit)
            hit = (
                select(CustomerSocialIdentity.customer_id)
                .where(
                    window_ok,
                    cls._cmp(CustomerSocialIdentity.identity_code, op, value),
                )
                .distinct()
            )
            return Customer.id.in_(hit)
        if field == "is_birthday_today":
            if op not in ("eq", "ne"):
                raise AppException(40001, "is_birthday_today 仅支持 eq/ne")
            # SQL 仅预筛「填过生日」；公历/农历精确匹配在 estimate/materialize/iter 后置过滤
            return Customer.birthday.isnot(None)
        if field == "is_member_anniversary_today":
            if op not in ("eq", "ne"):
                raise AppException(40001, "is_member_anniversary_today 仅支持 eq/ne")
            # SQL 预筛：无生日 + 入会锚点月日≈今日（满1年）；2/29→非闰年2/28 与精确判定在后置过滤
            from app.utils.birthday import today_beijing

            day = today_beijing()
            anchor = func.coalesce(Customer.member_activated_at, Customer.created_at)
            md_match = or_(
                and_(
                    extract("month", anchor) == day.month,
                    extract("day", anchor) == day.day,
                ),
                and_(
                    day.month == 2,
                    day.day == 28,
                    extract("month", anchor) == 2,
                    extract("day", anchor) == 29,
                ),
            )
            return and_(
                Customer.birthday.is_(None),
                anchor.isnot(None),
                extract("year", anchor) <= (day.year - 1),
                md_match,
            )
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
        want_bday, want_anniv = cls._day_post_filter_wants(rules_json)
        from app.services.customer_service import CustomerService

        if want_bday is None and want_anniv is None:
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
        else:
            # 生日/入会周年精确匹配需拉候选后内存过滤（生日支持农历）
            candidates = list(
                (await db.execute(select(Customer).where(filt).order_by(Customer.id.desc())))
                .scalars()
                .all()
            )
            matched = [
                c
                for c in candidates
                if cls._customer_matches_day_post_filters(
                    c, want_bday=want_bday, want_anniv=want_anniv
                )
            ]
            total = len(matched)
            rows = matched[: max(0, min(int(sample_limit), 50))]

        samples = [
            {
                "id": int(c.id),
                "customer_no": c.customer_no,
                **CustomerService.identity_name_payload(c),
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
    async def hq_same_code_parallel_warning(
        cls,
        db: AsyncSession,
        *,
        store_id: int | None,
        code: str,
        exclude_id: int | None = None,
    ) -> str | None:
        """门店分群与总部同 code：类型 C 并行提示（不覆盖、不阻断）。"""
        if store_id is None:
            return None
        key = (code or "").strip()
        if not key:
            return None
        q = select(CustomerSegment).where(
            CustomerSegment.store_id.is_(None),
            CustomerSegment.code == key,
        )
        if exclude_id is not None:
            q = q.where(CustomerSegment.id != int(exclude_id))
        hq = (await db.execute(q.order_by(CustomerSegment.id.asc()).limit(1))).scalar_one_or_none()
        if not hq:
            return None
        return (
            f"总部已有同编码分群「{hq.name}」（code={key}）；"
            "分群为并行资源，本店与总部互不覆盖，发布活动请选对本店生效的分群"
        )

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
            # 总部预置分群仅超管（store_id=None 调用方）可改，避免门店改坏共用规则
            if row.store_id is None and store_id is not None:
                raise AppException(40301, "总部预置分群仅超管可修改")
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
        # TimestampMixin server_default：flush 后未加载 created_at/updated_at，同步 _payload 会 MissingGreenlet
        await db.refresh(row)
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
        want_bday, want_anniv = cls._day_post_filter_wants(seg.rules_json)
        need_day_post = want_bday is not None or want_anniv is not None
        await db.execute(delete(CustomerSegmentMember).where(CustomerSegmentMember.segment_id == seg.id))
        inserted = 0
        last_id = 0
        while True:
            if not need_day_post:
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
            else:
                batch = list(
                    (
                        await db.execute(
                            select(Customer)
                            .where(filt, Customer.id > last_id)
                            .order_by(Customer.id)
                            .limit(batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not batch:
                    break
                for c in batch:
                    if cls._customer_matches_day_post_filters(
                        c, want_bday=want_bday, want_anniv=want_anniv
                    ):
                        db.add(CustomerSegmentMember(segment_id=int(seg.id), customer_id=int(c.id)))
                        inserted += 1
                last_id = int(batch[-1].id)
                await db.flush()
                if len(batch) < batch_size:
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
        store_id: int | None = None,
    ):
        """分批产出顾客 id。

        STATIC 或已物化优先用快照；否则动态扫描。
        store_id：门店活动引用总部分群时传入本店 id，按本店过滤且不读总部快照（避免串店/覆盖）。
        """
        seg = await cls.get_segment(db, segment_id)
        # 门店活动 + 总部分群：强制动态按本店圈人
        force_store_dynamic = store_id is not None and seg.store_id is None
        use_snap = (
            prefer_snapshot
            and not force_store_dynamic
            and (
                (seg.segment_type or "").upper() == "STATIC" or seg.materialized_at is not None
            )
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
        filt_store = int(store_id) if force_store_dynamic else (
            int(seg.store_id) if seg.store_id is not None else None
        )
        filt = cls.build_filter(seg.rules_json, store_id=filt_store)
        want_bday, want_anniv = cls._day_post_filter_wants(seg.rules_json)
        need_day_post = want_bday is not None or want_anniv is not None
        last_id = 0
        while True:
            if not need_day_post:
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
            else:
                batch = list(
                    (
                        await db.execute(
                            select(Customer)
                            .where(filt, Customer.id > last_id)
                            .order_by(Customer.id)
                            .limit(batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not batch:
                    return
                ids = [
                    int(c.id)
                    for c in batch
                    if cls._customer_matches_day_post_filters(
                        c, want_bday=want_bday, want_anniv=want_anniv
                    )
                ]
                if ids:
                    yield ids
                last_id = int(batch[-1].id)
                if len(batch) < batch_size:
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
