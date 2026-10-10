from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success, serialize_dt
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    FaceProfile,
    Order,
)
from app.services.audit_service import AuditService
from app.services.customer_merge_service import CustomerMergeService
from app.services.customer_service import CustomerService, PreferenceService

router = APIRouter(prefix="/customers", tags=["顾客"])


class CustomerMergeBody(BaseModel):
    source_customer_id: int = Field(..., gt=0)
    reason: str | None = Field(None, max_length=255)


def _customer_in_store(store_id: int):
    """本店可见顾客：在本店下过单，或注册/首次到店记在本店。"""
    sid = int(store_id)
    return or_(
        Customer.registered_store_id == sid,
        Customer.first_seen_store_id == sid,
        Customer.id.in_(
            select(Order.customer_id).where(
                Order.store_id == sid,
                Order.customer_id.is_not(None),
            )
        ),
    )


async def _assert_customer_in_store(db, customer_id: int, store_id: int, *, action: str = "查看") -> None:
    hit = await db.scalar(
        select(Customer.id).where(Customer.id == int(customer_id), _customer_in_store(store_id)).limit(1)
    )
    if not hit:
        raise AppException(40301, f"无权{action}其他门店顾客")


@router.get("", dependencies=[require_permission("customer:read")])
async def list_customers(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    channel: str | None = None,
    include_disabled: bool = False,
    status: int | None = None,
    member_status: str | None = None,
    member_level_id: int | None = None,
    registered_from: str | None = None,
    registered_to: str | None = None,
    store_id: int | None = None,
):
    from datetime import datetime, timedelta

    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    # 门店账号仅看在本店下过单的顾客；超管可传 store_id 筛选或看全量
    effective_store = require_bound_store(user, store_id)

    q = select(Customer)
    if status is not None:
        q = q.where(Customer.status == int(status))
    elif not include_disabled:
        q = q.where(Customer.status == 1)
    if member_status and member_status.strip().upper() in ("GUEST", "MEMBER"):
        q = q.where(Customer.member_status == member_status.strip().upper())
    if member_level_id:
        q = q.where(Customer.member_level_id == int(member_level_id))
    if registered_from:
        try:
            q = q.where(Customer.created_at >= datetime.fromisoformat(registered_from))
        except ValueError:
            pass
    if registered_to:
        try:
            # 结束日含全天：+1 天取开区间
            q = q.where(Customer.created_at < datetime.fromisoformat(registered_to) + timedelta(days=1))
        except ValueError:
            pass
    if effective_store is not None:
        q = q.where(_customer_in_store(effective_store))
    if keyword and keyword.strip():
        q = q.where(CustomerService.keyword_condition(keyword))
    if channel:
        ch = channel.strip().upper()
        if ch == "WECHAT":
            q = q.where(
                Customer.id.in_(select(CustomerWechatAccount.customer_id).distinct())
            )
        elif ch == "ALIPAY":
            q = q.where(
                Customer.id.in_(select(CustomerAlipayAccount.customer_id).distinct())
            )

    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(Customer.created_at.desc(), Customer.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    customers = list(result.scalars().all())
    ids = [c.id for c in customers]

    order_counts: dict[int, int] = {}
    wechat_ids: set[int] = set()
    alipay_ids: set[int] = set()
    face_ids: set[int] = set()
    wx_avatar_map: dict[int, str] = {}
    ali_avatar_map: dict[int, str] = {}
    if ids:
        oc_q = (
            select(Order.customer_id, func.count())
            .where(Order.customer_id.in_(ids))
            .group_by(Order.customer_id)
        )
        if effective_store is not None:
            oc_q = oc_q.where(Order.store_id == effective_store)
        rows = await db.execute(oc_q)
        order_counts = {cid: int(cnt) for cid, cnt in rows.all()}
        wechat_ids = set(
            (
                await db.execute(
                    select(CustomerWechatAccount.customer_id).where(CustomerWechatAccount.customer_id.in_(ids))
                )
            ).scalars().all()
        )
        alipay_ids = set(
            (
                await db.execute(
                    select(CustomerAlipayAccount.customer_id).where(CustomerAlipayAccount.customer_id.in_(ids))
                )
            ).scalars().all()
        )
        for cid, av in (
            await db.execute(
                select(CustomerWechatAccount.customer_id, CustomerWechatAccount.avatar_url)
                .where(
                    CustomerWechatAccount.customer_id.in_(ids),
                    CustomerWechatAccount.avatar_url.is_not(None),
                    CustomerWechatAccount.avatar_url != "",
                )
                .order_by(CustomerWechatAccount.id.desc())
            )
        ).all():
            if cid not in wx_avatar_map and av:
                wx_avatar_map[int(cid)] = str(av).strip()
        for cid, av in (
            await db.execute(
                select(CustomerAlipayAccount.customer_id, CustomerAlipayAccount.avatar_url)
                .where(
                    CustomerAlipayAccount.customer_id.in_(ids),
                    CustomerAlipayAccount.avatar_url.is_not(None),
                    CustomerAlipayAccount.avatar_url != "",
                )
                .order_by(CustomerAlipayAccount.id.desc())
            )
        ).all():
            if cid not in ali_avatar_map and av:
                ali_avatar_map[int(cid)] = str(av).strip()
        face_ids = set(
            (
                await db.execute(
                    select(FaceProfile.customer_id).where(
                        FaceProfile.customer_id.in_(ids),
                        FaceProfile.consent_status == "AUTHORIZED",
                    )
                )
            ).scalars().all()
        )

    # 智能口味：列表与详情一致，返回全部偏好（按分数排序）
    pref_map: dict[int, list[dict]] = {i: [] for i in ids}
    if ids:
        pref_rows = (
            await db.execute(
                select(CustomerPreference)
                .where(CustomerPreference.customer_id.in_(ids))
                .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
            )
        ).scalars().all()
        for p in pref_rows:
            pref_map.setdefault(p.customer_id, []).append(
                {
                    "category": p.category,
                    "option_code": p.option_code,
                    "option_name": p.option_name or p.option_code,
                    "score": float(p.preference_score),
                    "order_count": int(p.order_count or 0),
                    "label": f"{p.category}·{(p.option_name or p.option_code)}"
                    if p.category
                    else str(p.option_name or p.option_code),
                }
            )

    level_map = await CustomerService.load_levels_map(db, [c.member_level_id for c in customers])
    scan_map = await CustomerService.registered_scan_map(db, customers)

    def _avatar_for(c: Customer) -> str | None:
        raw = str(c.avatar_url or "").strip()
        if raw:
            return raw
        return wx_avatar_map.get(c.id) or ali_avatar_map.get(c.id)

    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            "display_name": c.display_name or c.nickname,
            "nickname": c.nickname,
            "avatar_url": _avatar_for(c),
            "phone": c.phone,
            "status": c.status,
            "channels": [
                *(["WECHAT"] if c.id in wechat_ids else []),
                *(["ALIPAY"] if c.id in alipay_ids else []),
            ],
            "has_face": c.id in face_ids,
            "preferences": pref_map.get(c.id, []),
            "order_count": order_counts.get(c.id, 0),
            "last_order_at": serialize_dt(c.last_order_at),
            "created_at": serialize_dt(c.created_at),
            **scan_map.get(c.id, {}),
            **CustomerService.member_payload(c, level_map.get(c.member_level_id) if c.member_level_id else None),
        }
        for c in customers
    ]
    return success(
        {"items": items, "total": total, "page": page, "page_size": page_size, "total_pages": calc_pages(total, page_size)},
        request=request,
    )


@router.get("/{customer_id}", dependencies=[require_permission("customer:read")])
async def get_customer(customer_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import require_bound_store

    customer = await db.get(Customer, customer_id)
    if not customer:
        raise AppException(40401, "顾客不存在")

    # 门店账号仅可查看本店顾客（下过单，或注册/首次到店在本店）；超管可看全量
    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        await _assert_customer_in_store(db, customer_id, effective_store)

    wechat = await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer_id))
    alipay = await db.execute(select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == customer_id))
    face = await db.execute(
        select(FaceProfile).where(
            FaceProfile.customer_id == customer_id,
            FaceProfile.consent_status == "AUTHORIZED",
        )
    )
    prefs = await PreferenceService.get_recommendations(db, customer_id, store_id=effective_store)
    pref_list = prefs.get("preferences") or []
    all_prefs = list(
        (
            await db.execute(
                select(CustomerPreference)
                .where(CustomerPreference.customer_id == customer_id)
                .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
            )
        ).scalars().all()
    )
    order_count_q = select(func.count()).select_from(Order).where(Order.customer_id == customer_id)
    if effective_store is not None:
        order_count_q = order_count_q.where(Order.store_id == effective_store)
    order_count = await db.scalar(order_count_q) or 0
    profile = await CustomerService.build_member_profile(db, customer)
    wechat_list = list(wechat.scalars().all())
    alipay_list = list(alipay.scalars().all())
    scan = (await CustomerService.registered_scan_map(db, [customer])).get(customer.id) or {}
    avatar = str(customer.avatar_url or "").strip() or None
    if not avatar:
        for w in wechat_list:
            a = str(getattr(w, "avatar_url", None) or "").strip()
            if a:
                avatar = a
                break
    if not avatar:
        for a_row in alipay_list:
            a = str(getattr(a_row, "avatar_url", None) or "").strip()
            if a:
                avatar = a
                break
    return success(
        {
            "id": customer.id,
            "customer_no": customer.customer_no,
            "display_name": customer.display_name or customer.nickname,
            "nickname": customer.nickname,
            "phone": customer.phone,
            "status": customer.status,
            "avatar_url": avatar,
            "has_face": face.scalar_one_or_none() is not None,
            "order_count": int(order_count),
            "last_order_at": serialize_dt(customer.last_order_at),
            "created_at": serialize_dt(customer.created_at),
            **scan,
            **profile,
            "wechat_accounts": [
                {
                    "openid": w.openid,
                    "unionid": w.unionid,
                    "nickname": getattr(w, "nickname", None),
                    "avatar_url": getattr(w, "avatar_url", None),
                    "profile_completed": int(getattr(w, "profile_completed", 0) or 0),
                    "is_mock": str(w.openid).startswith("wx_dev_"),
                }
                for w in wechat_list
            ],
            "alipay_accounts": [
                {
                    "alipay_user_id": a.alipay_user_id,
                    "nickname": getattr(a, "nickname", None),
                    "avatar_url": getattr(a, "avatar_url", None),
                    "profile_completed": int(getattr(a, "profile_completed", 0) or 0),
                    "is_mock": str(a.alipay_user_id).startswith("ali_dev_"),
                }
                for a in alipay_list
            ],
            "preferences": [
                {
                    "category": p.category,
                    "option_code": p.option_code,
                    "option_name": getattr(p, "option_name", None) or p.option_code,
                    "score": float(p.preference_score),
                    "order_count": int(p.order_count or 0),
                    "label": f"{p.category}·{(getattr(p, 'option_name', None) or p.option_code)}"
                    if p.category
                    else str(getattr(p, "option_name", None) or p.option_code),
                    "last_used_at": serialize_dt(p.last_used_at),
                }
                for p in all_prefs
            ],
            "recommendations": [
                {
                    "category": p.get("category"),
                    "option_code": p.get("option_code"),
                    "option_name": p.get("option_name") or p.get("option_code"),
                    "score": p.get("score"),
                    "order_count": p.get("order_count"),
                    "label": p.get("label")
                    or f"{p.get('category')}·{p.get('option_name') or p.get('option_code')}",
                }
                for p in pref_list
                if isinstance(p, dict)
            ],
            "welcome_hint": (
                "欢迎回来。今天还是老样子："
                + "、".join(
                    (
                        x.get("label")
                        or f"{x.get('category')}·{x.get('option_name') or x.get('option_code')}"
                    )
                    for x in pref_list[:2]
                    if isinstance(x, dict)
                )
                + "？"
                if pref_list
                else None
            ),
            "preference_scene": prefs.get("scene") or {"enabled": False},
        },
        request=request,
    )


@router.post("/{target_id}/merge", dependencies=[require_permission("customer:write")])
async def merge_customers(
    target_id: int,
    body: CustomerMergeBody,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """合并源顾客到目标：SUPER_ADMIN 或 customer:write；非超管要求两顾客均在本店下过单。"""
    from app.services.store_scope import require_bound_store

    if int(body.source_customer_id) == int(target_id):
        raise AppException(40061, "不能合并同一顾客")

    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        for cid in (target_id, body.source_customer_id):
            await _assert_customer_in_store(db, cid, effective_store, action="合并")

    result = await CustomerMergeService.merge(
        db,
        target_customer_id=target_id,
        source_customer_id=body.source_customer_id,
        reason=body.reason,
        operator_id=user.id,
        request=request,
        actor_store_id=int(effective_store) if effective_store else None,
    )
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="customer.merge",
        module="customer",
        target_type="customer",
        target_id=int(target_id),
        detail={
            "source_id": int(body.source_customer_id),
            "target_id": int(target_id),
            "reason": body.reason,
            "store_id": int(effective_store) if effective_store else None,
            "matched_fields": list(result.keys()) if isinstance(result, dict) else None,
        },
        request=request,
        store_id=int(effective_store) if effective_store else None,
    )
    return success(result, message="合并成功", request=request)


@router.get("/{customer_id}/preferences")
async def get_preferences(
    customer_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = None,
):
    if customer.id != customer_id:
        raise AppException(40301, "无权查看他人偏好", status_code=403)
    prefs = await PreferenceService.get_recommendations(db, customer_id, store_id=store_id)
    result = await db.execute(
        select(CustomerPreference)
        .where(CustomerPreference.customer_id == customer_id)
        .order_by(CustomerPreference.preference_score.desc(), CustomerPreference.order_count.desc())
    )
    prefs["all"] = [
        {
            "category": p.category,
            "option_code": p.option_code,
            "option_name": p.option_name or p.option_code,
            "score": float(p.preference_score),
            "order_count": p.order_count,
        }
        for p in result.scalars()
    ]
    return success(prefs, request=request)
