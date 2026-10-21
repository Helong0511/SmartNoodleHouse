import re
from datetime import date

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.api.customers_common import (
    assert_customer_in_store as _assert_customer_in_store,
    customer_in_store as _customer_in_store,
    list_customer_favorites as _list_customer_favorites,
)
from app.api.deps import CurrentCustomer, CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, money, success, serialize_dt
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerLifecycle,
    CustomerPreference,
    CustomerWechatAccount,
    FaceProfile,
    Order,
)
from app.services.audit_service import AuditService
from app.services.customer_merge_service import CustomerMergeService
from app.services.customer_service import CustomerService, PreferenceService

router = APIRouter(prefix="/customers", tags=["顾客"])

_PHONE_RE = re.compile(r"^1\d{10}$")


class CustomerMergeBody(BaseModel):
    source_customer_id: int = Field(..., gt=0)
    reason: str | None = Field(None, max_length=255)


class CustomerUpdate(BaseModel):
    """员工编辑顾客档案。未传字段不改；显式 null 可清空可空字段（phone/birthday）。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=100)
    nickname: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    status: int | None = Field(default=None, ge=0, le=1, description="1 启用 / 0 禁用")
    birthday: date | None = None
    birthday_calendar: str | None = Field(
        default=None, description="SOLAR=公历 / LUNAR=农历；清空生日时忽略"
    )
    recommend_opt_in: int | None = Field(default=None, ge=0, le=1)

    @field_validator("display_name", "nickname", mode="before")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if not _PHONE_RE.match(s):
            raise ValueError("手机号须为 11 位中国大陆号段")
        return s

    @field_validator("birthday_calendar", mode="before")
    @classmethod
    def normalize_calendar(cls, v):
        if v is None:
            return None
        from app.utils.birthday import VALID_CALENDARS, normalize_calendar

        s = str(v).strip().upper()
        if not s:
            return None
        if s not in VALID_CALENDARS:
            raise ValueError("生日历法须为 SOLAR（公历）或 LUNAR（农历）")
        return normalize_calendar(s)


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
    birthday_today: bool = False,
    lifecycle: str | None = None,
    rfm_segment: str | None = None,
    silence_days_min: int | None = None,
):
    from datetime import datetime, timedelta

    from app.core.response import now_local
    from app.services.store_scope import require_bound_store
    from app.utils.birthday import (
        birthday_for_picker,
        format_birthday_display,
        is_birthday_today,
        normalize_calendar,
    )

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
    lc_code = (lifecycle or "").strip().upper()
    if lc_code:
        q = q.where(
            Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    CustomerLifecycle.lifecycle_code == lc_code
                )
            )
        )
    rfm = (rfm_segment or "").strip().upper()
    if rfm:
        q = q.where(
            Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    CustomerLifecycle.rfm_segment == rfm
                )
            )
        )
    if silence_days_min is not None and int(silence_days_min) > 0:
        q = q.where(
            Customer.id.in_(
                select(CustomerLifecycle.customer_id).where(
                    CustomerLifecycle.silence_days >= int(silence_days_min)
                )
            )
        )
    if birthday_today:
        # 公历/农历月日匹配需应用层判定；先收窄到有生日的顾客再过滤
        bday_q = select(Customer.id, Customer.birthday, Customer.birthday_calendar).where(
            Customer.birthday.is_not(None)
        )
        if effective_store is not None:
            bday_q = bday_q.where(_customer_in_store(effective_store))
        today = now_local().date()
        hit_ids: list[int] = []
        for cid, bday, cal in (await db.execute(bday_q)).all():
            if is_birthday_today(bday, normalize_calendar(cal), today=today):
                hit_ids.append(int(cid))
        if not hit_ids:
            return success(
                {
                    "items": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": 0,
                    "birthday_today": True,
                    "birthday_benefit": await CustomerService.birthday_benefit_payload(
                        db, effective_store
                    ),
                },
                request=request,
            )
        q = q.where(Customer.id.in_(hit_ids))

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
    lc_map: dict[int, CustomerLifecycle] = {}
    if ids:
        for row in (
            await db.execute(
                select(CustomerLifecycle).where(CustomerLifecycle.customer_id.in_(ids))
            )
        ).scalars().all():
            lc_map[int(row.customer_id)] = row

    def _avatar_for(c: Customer) -> str | None:
        raw = str(c.avatar_url or "").strip()
        if raw:
            return raw
        return wx_avatar_map.get(c.id) or ali_avatar_map.get(c.id)

    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            **CustomerService.identity_name_payload(c),
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
            "birthday": c.birthday.isoformat() if c.birthday else None,
            "birthday_calendar": normalize_calendar(getattr(c, "birthday_calendar", None)),
            "birthday_display": format_birthday_display(
                c.birthday, getattr(c, "birthday_calendar", None)
            ),
            "birthday_picker": birthday_for_picker(
                c.birthday, getattr(c, "birthday_calendar", None)
            ),
            "is_birthday_today": is_birthday_today(
                c.birthday,
                getattr(c, "birthday_calendar", None),
                today=now_local().date(),
            ),
            "lifecycle_code": (lc_map[c.id].lifecycle_code if c.id in lc_map else None),
            "rfm_segment": (lc_map[c.id].rfm_segment if c.id in lc_map else None),
            "silence_days": (lc_map[c.id].silence_days if c.id in lc_map else None),
            **scan_map.get(c.id, {}),
            **CustomerService.member_payload(c, level_map.get(c.member_level_id) if c.member_level_id else None),
        }
        for c in customers
    ]
    from app.api.deps import get_user_permissions
    from app.services.field_policy import apply_field_policy_list

    perms = await get_user_permissions(db, user.role_id, user_id=user.id)
    items = apply_field_policy_list(
        items, perms, is_super_admin=user.role.code == "SUPER_ADMIN"
    )
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
            "birthday_today": bool(birthday_today),
            "birthday_benefit": await CustomerService.birthday_benefit_payload(
                db, effective_store
            ),
        },
        request=request,
    )


@router.get("/{customer_id}/lifecycle", dependencies=[require_permission("customer:read")])
async def get_customer_lifecycle(
    customer_id: int, request: Request, db: DbSession, user: CurrentUser
):
    """生命周期 / RFM 只读（优先夜批快照，无快照则即时估算）。"""
    from app.services.lifecycle_service import LifecycleService
    from app.services.store_scope import require_bound_store

    require_bound_store(user, None)
    customer = await db.get(Customer, int(customer_id))
    if not customer:
        raise AppException(40401, "顾客不存在")
    data = await LifecycleService.get_payload(db, int(customer_id))
    return success(data, request=request)


@router.get("/{customer_id}/service-card", dependencies=[require_permission("customer:read")])
async def get_service_card(customer_id: int, request: Request, db: DbSession, user: CurrentUser):
    """POS/迎宾服务卡：脱敏摘要，不含证件原图。"""
    from app.services.service_card_service import ServiceCardService
    from app.services.store_scope import require_bound_store

    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        await _assert_customer_in_store(db, customer_id, effective_store)
    card = await ServiceCardService.build(db, customer_id, store_id=effective_store)
    return success(card, request=request)


@router.get("/{customer_id}", dependencies=[require_permission("customer:read")])
async def get_customer(customer_id: int, request: Request, db: DbSession, user: CurrentUser):
    from app.services.store_scope import require_bound_store
    from app.utils.birthday import (
        birthday_for_picker,
        format_birthday_display,
        normalize_calendar,
    )

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
    birthday_cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
    birthday_benefit = await CustomerService.birthday_benefit_payload(
        db, effective_store, customer
    )
    return success(
        {
            "id": customer.id,
            "customer_no": customer.customer_no,
            **CustomerService.identity_name_payload(customer),
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
                    "base_score": p.get("base_score"),
                    "scene_boosted": bool(p.get("scene_boosted")),
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
            "birthday": customer.birthday.isoformat() if customer.birthday else None,
            "birthday_picker": birthday_for_picker(customer.birthday, birthday_cal),
            "birthday_calendar": birthday_cal,
            # 顶层展示文案；日期控件 value 用 birthday_picker（农历为农历数字）
            "birthday_display": format_birthday_display(customer.birthday, birthday_cal),
            "birthday_locked": int(getattr(customer, "birthday_locked", 0) or 0) == 1,
            "birthday_benefit": birthday_benefit,
            "recommend_opt_in": int(customer.recommend_opt_in or 0),
            "favorites": await _list_customer_favorites(db, customer_id, store_id=effective_store),
        },
        request=request,
    )


@router.patch("/{customer_id}", dependencies=[require_permission("customer:write")])
async def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """员工编辑顾客基础信息（昵称/展示名/手机/启用状态/生日/推荐开关）。"""
    from app.core.response import now_local
    from app.services.store_scope import require_bound_store

    customer = await db.get(Customer, customer_id)
    if not customer:
        raise AppException(40401, "顾客不存在")
    effective_store = require_bound_store(user, None)
    if effective_store is not None:
        await _assert_customer_in_store(db, customer_id, effective_store, action="编辑")

    fields = body.model_fields_set
    if not fields:
        raise AppException(40001, "未提交任何可修改字段")

    changes: dict = {}
    # 展示名 = 员工侧；昵称 = 渠道/小程序侧。仅真实变更才记审计；仅昵称变更才同步渠道。
    nickname_changed = False
    if "display_name" in fields:
        old_display = customer.display_name
        new_display = body.display_name
        if not CustomerService.names_equal(old_display, new_display):
            changes["display_name"] = {"old": old_display, "new": new_display}
            customer.display_name = new_display
    if "nickname" in fields:
        old_nick = customer.nickname
        new_nick = body.nickname
        if not CustomerService.names_equal(old_nick, new_nick):
            changes["nickname"] = {"old": old_nick, "new": new_nick}
            customer.nickname = new_nick
            nickname_changed = True
    if nickname_changed and CustomerService.is_meaningful_nickname(customer.nickname):
        await CustomerService.sync_nickname_to_channel_accounts(
            db, customer, nickname=str(customer.nickname).strip()
        )
    if "phone" in fields:
        new_phone = body.phone
        old_phone = customer.phone
        if not CustomerService.names_equal(old_phone, new_phone):
            if new_phone:
                dup = await db.scalar(
                    select(Customer.id).where(
                        Customer.phone == new_phone,
                        Customer.id != int(customer_id),
                        Customer.status == 1,
                    ).limit(1)
                )
                if dup:
                    raise AppException(40002, "该手机号已被其他顾客使用")
            changes["phone"] = {"old": old_phone, "new": new_phone}
            customer.phone = new_phone
            if new_phone:
                customer.phone_verified = int(customer.phone_verified or 0)
            else:
                customer.phone_verified = 0
    if "status" in fields and body.status is not None:
        old_status = int(customer.status or 0)
        new_status = int(body.status)
        if old_status != new_status:
            changes["status"] = {"old": old_status, "new": new_status}
            customer.status = new_status
            if new_status == 0:
                # 禁用时抬升 token_version，使既有顾客 JWT 失效
                customer.token_version = int(getattr(customer, "token_version", 0) or 0) + 1
                customer.account_status = "DISABLED"
            elif str(getattr(customer, "account_status", "") or "").upper() == "DISABLED":
                customer.account_status = "ACTIVE"
    if "birthday_calendar" in fields and body.birthday_calendar is not None:
        from app.utils.birthday import convert_birthday_calendar, normalize_calendar

        old_cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
        new_cal = normalize_calendar(body.birthday_calendar)
        changes["birthday_calendar"] = {"old": old_cal, "new": new_cal}
        # 仅改历法（未同时改日期）：同日互转，存储公历日不变
        if (
            "birthday" not in fields
            and customer.birthday is not None
            and old_cal != new_cal
        ):
            converted = convert_birthday_calendar(customer.birthday, old_cal, new_cal)
            if converted is not None and converted != customer.birthday:
                changes["birthday"] = {
                    "old": customer.birthday.isoformat(),
                    "new": converted.isoformat(),
                    "converted_by_calendar": True,
                }
                customer.birthday = converted
        customer.birthday_calendar = new_cal
    if "birthday" in fields:
        from app.utils.birthday import normalize_calendar, resolve_birthday_storage

        if body.birthday is None:
            changes["birthday"] = {
                "old": customer.birthday.isoformat() if customer.birthday else None,
                "new": None,
            }
            customer.birthday = None
            customer.birthday_calendar = "SOLAR"
            if int(getattr(customer, "birthday_locked", 0) or 0):
                changes["birthday_locked"] = {"old": 1, "new": 0}
            customer.birthday_locked = 0
        else:
            cal = normalize_calendar(getattr(customer, "birthday_calendar", None))
            try:
                stored = resolve_birthday_storage(body.birthday, cal)
            except ValueError as exc:
                raise AppException(
                    40030,
                    "农历日期无效，请重选" if cal == "LUNAR" else "生日格式不正确",
                ) from exc
            if stored > now_local().date():
                raise AppException(40031, "生日不能晚于今天")
            changes["birthday"] = {
                "old": customer.birthday.isoformat() if customer.birthday else None,
                "new": stored.isoformat(),
                "input_calendar": cal,
            }
            customer.birthday = stored
            # 后台写入生日后一并锁定，避免顾客自行再改
            if not int(getattr(customer, "birthday_locked", 0) or 0):
                changes["birthday_locked"] = {"old": 0, "new": 1}
            customer.birthday_locked = 1
    if "recommend_opt_in" in fields and body.recommend_opt_in is not None:
        old_opt = int(customer.recommend_opt_in or 0)
        new_opt = int(body.recommend_opt_in)
        if old_opt != new_opt:
            changes["recommend_opt_in"] = {"old": old_opt, "new": new_opt}
            customer.recommend_opt_in = new_opt

    if not changes:
        # 提交字段与现值相同：不写审计、不空刷渠道
        return await get_customer(customer_id, request, db, user)

    await db.flush()
    await AuditService.log_staff(
        db,
        user=user,
        action="customer.update",
        module="customer",
        target_type="customer",
        target_id=int(customer_id),
        detail={"changes": changes},
        request=request,
        store_id=int(effective_store) if effective_store else None,
    )
    # 复用详情接口数据形状，便于前端刷新抽屉
    return await get_customer(customer_id, request, db, user)


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
