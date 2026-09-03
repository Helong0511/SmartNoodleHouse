from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.response import calc_pages, success
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerPreference,
    CustomerWechatAccount,
    FaceProfile,
    Order,
)
from app.services.customer_service import CustomerService, PreferenceService

router = APIRouter(prefix="/customers", tags=["顾客"])


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
    store_id: int | None = None,
):
    from app.services.store_scope import require_bound_store

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    # 门店账号仅看在本店下过单的顾客；超管可传 store_id 筛选或看全量
    effective_store = require_bound_store(user, store_id)

    q = select(Customer)
    if not include_disabled:
        q = q.where(Customer.status == 1)
    if effective_store is not None:
        q = q.where(
            Customer.id.in_(
                select(Order.customer_id).where(
                    Order.store_id == effective_store,
                    Order.customer_id.is_not(None),
                ).distinct()
            )
        )
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
        q.order_by(Customer.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
            "last_order_at": c.last_order_at.isoformat() if c.last_order_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
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
async def get_customer(customer_id: int, request: Request, db: DbSession):
    customer = await db.get(Customer, customer_id)
    if not customer:
        return success(None, message="顾客不存在", request=request)
    wechat = await db.execute(select(CustomerWechatAccount).where(CustomerWechatAccount.customer_id == customer_id))
    alipay = await db.execute(select(CustomerAlipayAccount).where(CustomerAlipayAccount.customer_id == customer_id))
    face = await db.execute(
        select(FaceProfile).where(
            FaceProfile.customer_id == customer_id,
            FaceProfile.consent_status == "AUTHORIZED",
        )
    )
    prefs = await PreferenceService.get_recommendations(db, customer_id)
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
    order_count = await db.scalar(
        select(func.count()).select_from(Order).where(Order.customer_id == customer_id)
    ) or 0
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
            "last_order_at": customer.last_order_at.isoformat() if customer.last_order_at else None,
            "created_at": customer.created_at.isoformat() if customer.created_at else None,
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
                    "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
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
        },
        request=request,
    )


@router.get("/{customer_id}/preferences")
async def get_preferences(
    customer_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    if customer.id != customer_id:
        raise HTTPException(status_code=403, detail="无权查看他人偏好")
    prefs = await PreferenceService.get_recommendations(db, customer_id)
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


@router.post("/wechat/login")
async def wechat_login_deprecated():
    raise HTTPException(status_code=410, detail="已废弃，请使用 POST /api/v1/miniprogram/wechat/login 并传入 code")


@router.post("/alipay/login")
async def alipay_login_deprecated():
    raise HTTPException(status_code=410, detail="已废弃，请使用 POST /api/v1/miniprogram/alipay/login 并传入 auth_code")
