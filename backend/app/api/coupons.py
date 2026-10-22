from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import calc_pages, success, serialize_dt
from app.models import CouponTemplate, Customer
from sqlalchemy import or_, select
from app.schemas.coupon import CouponIssueRequest, CouponTemplateCreate, CouponTemplateUpdate
from app.services.audit_service import AuditService
from app.services.coupon_service import CouponService
from app.services.customer_service import CustomerService
from app.services.scope_override import hq_override_warning
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/coupons", tags=["优惠券"])


class TemplateStatusUpdate(BaseModel):
    status: int = Field(..., ge=0, le=1)


class MilestoneRewardItem(BaseModel):
    threshold_orders: int = Field(..., ge=1, le=100000, description="累计成功支付单数阈值")
    template_id: int = Field(..., ge=1)
    status: int = Field(default=1, ge=0, le=1)


class MilestoneRewardsReplace(BaseModel):
    store_id: int | None = Field(default=None, description="超管指定门店；门店账号忽略")
    items: list[MilestoneRewardItem] = Field(default_factory=list)


def _assert_template_manage_access(user, tmpl: CouponTemplate) -> None:
    """门店账号仅可改本店模板；总部模板仅超管可改。"""
    if user.role.code == "SUPER_ADMIN":
        return
    bound = require_bound_store(user, None)
    sid = getattr(tmpl, "store_id", None)
    if sid is None:
        raise AppException(40301, "无权修改总部优惠券模板")
    if bound is None or int(sid) != int(bound):
        raise AppException(40301, "无权修改其他门店优惠券模板")


def _resolve_milestone_store(user, store_id: int | None) -> int:
    bound = require_bound_store(user, None)
    if user.role.code == "SUPER_ADMIN":
        sid = int(store_id) if store_id else 0
        if sid < 1:
            raise AppException(40002, "请指定门店")
        return sid
    if bound is None:
        raise AppException(40301, "账号未绑定门店")
    return int(bound)

@router.get("/templates", dependencies=[require_permission("coupon:read")])
async def list_templates(
    request: Request, db: DbSession, user: CurrentUser, include_disabled: bool = False
):
    """优惠券模板列表：门店见「总部 ∪ 本店」；超管见全部。include_disabled 控制是否含停用模板。"""
    store_id = require_bound_store(user, None)
    items = await CouponService.list_templates(db, include_disabled=include_disabled, store_id=store_id)
    return success({"items": items}, request=request)


@router.post("/templates", dependencies=[require_permission("coupon:manage")])
async def create_template(data: CouponTemplateCreate, request: Request, db: DbSession, user: CurrentUser):
    payload = data.model_dump()
    if user.role.code == "SUPER_ADMIN":
        # 超管可建总部(NULL)或指定店
        pass
    else:
        bound = require_bound_store(user, None)
        if bound is None:
            raise AppException(40301, "账号未绑定门店，无法创建模板")
        payload["store_id"] = int(bound)
        # P2-4 门店白名单仅总部可配置
        payload.pop("store_whitelist", None)
    tmpl = await CouponService.create_template(db, payload)
    warn = await CouponService.hq_name_override_warning(
        db, store_id=tmpl.store_id, name=tmpl.name
    )
    await db.commit()
    await AuditService.log(
        db,
        user_id=user.id,
        action="coupon.template.create",
        module="coupon",
        target_type="coupon_template",
        target_id=tmpl.id,
        detail={"name": tmpl.name, "coupon_type": tmpl.coupon_type, "store_id": tmpl.store_id},
        request=request,
    )
    body = CouponService.template_payload(tmpl)
    if warn:
        body["override_warning"] = warn
    return success(body, request=request)


@router.put("/templates/{template_id}", dependencies=[require_permission("coupon:manage")])
async def update_template(
    template_id: int,
    data: CouponTemplateUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    tmpl = await db.get(CouponTemplate, template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    _assert_template_manage_access(user, tmpl)
    raw = data.model_dump(exclude_unset=True)
    if user.role.code != "SUPER_ADMIN":
        raw.pop("store_id", None)  # 门店不可改归属
        raw.pop("store_whitelist", None)  # P2-4 门店白名单仅总部可配置
    tmpl = await CouponService.update_template(db, tmpl, raw)
    warn = await CouponService.hq_name_override_warning(
        db, store_id=tmpl.store_id, name=tmpl.name
    )
    await db.commit()
    await AuditService.log(
        db,
        user_id=user.id,
        action="coupon.template.update",
        module="coupon",
        target_type="coupon_template",
        target_id=tmpl.id,
        detail={"changes": raw},
        request=request,
    )
    body = CouponService.template_payload(tmpl)
    if warn:
        body["override_warning"] = warn
    return success(body, request=request)


@router.patch("/templates/{template_id}/status", dependencies=[require_permission("coupon:manage")])
async def set_template_status(
    template_id: int,
    data: TemplateStatusUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    tmpl = await db.get(CouponTemplate, template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    _assert_template_manage_access(user, tmpl)
    old_status = int(tmpl.status)
    tmpl.status = int(data.status)
    await db.commit()
    await AuditService.log(
        db,
        user_id=user.id,
        action="coupon.template.status",
        module="coupon",
        target_type="coupon_template",
        target_id=tmpl.id,
        detail={"from": old_status, "to": int(data.status)},
        request=request,
    )
    return success(CouponService.template_payload(tmpl), request=request)


@router.get("/issued", dependencies=[require_permission("coupon:read")])
async def list_issued_coupons(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    customer_id: int | None = None,
    template_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    issued_store_id: int | None = None,
    coupon_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    """顾客持券记录（全状态）。门店账号仅看本店相关顾客；issued_store_id 按发券门店过滤。"""
    store_id = require_bound_store(user, None)
    # 防御：发券门店筛选仅超管可用；门店账号丢弃该参数，沿用本店数据隔离口径
    if user.role.code != "SUPER_ADMIN":
        issued_store_id = None
    from app.schemas.pagination import clamp_page

    page, page_size = clamp_page(page, page_size)
    items, total = await CouponService.list_customer_coupons_admin(
        db,
        customer_id=customer_id,
        template_id=template_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
        store_id=store_id,
        issued_store_id=issued_store_id,
        coupon_type=coupon_type,
    )
    return success(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
        },
        request=request,
    )


@router.get("/customers/search", dependencies=[require_permission("coupon:manage")])
async def search_customers_for_issue(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str = Query(..., min_length=1, max_length=100, description="昵称/手机/顾客编号"),
    page_size: int = 20,
):
    """手动发券：按门店隔离搜索顾客（本店下过单或本店注册）。超管可搜全库。"""
    store_id = require_bound_store(user, None)
    rows, _total = await CustomerService.search_active_customers(
        db, keyword, page=1, page_size=page_size, store_id=store_id
    )
    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            **CustomerService.identity_name_payload(c),
            "phone": c.phone,
        }
        for c in rows
    ]
    return success({"items": items}, request=request)


@router.post("/issue", dependencies=[require_permission("coupon:manage")])
async def issue_coupon(data: CouponIssueRequest, request: Request, db: DbSession, user: CurrentUser):
    """向指定顾客发放优惠券/代金券。门店仅可对本店顾客发「总部∪本店」模板。"""
    store_id = require_bound_store(user, None)
    if store_id is not None:
        from app.api.customers import _assert_customer_in_store

        customer = await db.get(Customer, data.customer_id)
        if not customer:
            raise AppException(40401, "顾客不存在")
        # 与顾客列表同一口径：本店注册、首次到店或在本店下过单
        await _assert_customer_in_store(db, data.customer_id, int(store_id), action="发券")
    tmpl = await db.get(CouponTemplate, data.template_id)
    if not tmpl:
        raise AppException(40407, "优惠券模板不存在")
    if not CouponService.template_visible_to_store(tmpl, store_id):
        raise AppException(40301, "无权使用其他门店的优惠券模板")
    issued = await CouponService.issue_by_template_id(
        db,
        data.customer_id,
        data.template_id,
        quantity=data.quantity,
        client_request_id=data.client_request_id,
        issued_by_user_id=int(user.id) if getattr(user, "id", None) else None,
        issued_at_store_id=store_id,
    )
    await db.commit()
    kind = "代金券" if CouponService.is_voucher_type(tmpl.coupon_type) else "优惠券"
    items = [
        {
            "id": cc.id,
            "coupon_no": cc.coupon_no,
            "customer_id": cc.customer_id,
            "template_id": cc.template_id,
            "template_name": tmpl.name if tmpl else None,
            "summary": CouponService.summary_label(tmpl) if tmpl else None,
            "status": cc.status,
            "expires_at": serialize_dt(cc.expires_at),
        }
        for cc in issued
    ]
    qty = len(items)
    await AuditService.log_staff(
        db,
        user=user,
        action="coupon.issue",
        module="coupon",
        target_type="customer",
        target_id=int(data.customer_id),
        detail={
            "customer_id": int(data.customer_id),
            "template_id": int(data.template_id),
            "quantity": int(qty),
            "issued_coupon_ids": [int(cc.id) for cc in issued],
            "store_id": int(store_id) if store_id else None,
        },
        request=request,
        store_id=int(store_id) if store_id else None,
    )
    return success(
        {
            "quantity": qty,
            "items": items,
            "template_name": tmpl.name if tmpl else None,
            "coupon_type": tmpl.coupon_type if tmpl else None,
        },
        message=f"已发放 {qty} 张{kind}",
        request=request,
    )


@router.get("/milestones", dependencies=[require_permission("coupon:read")])
async def list_milestones(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """门店累计成单里程碑发券规则。"""
    sid = _resolve_milestone_store(user, store_id)
    items = await CouponService.list_milestone_rewards(db, sid, include_disabled=True)
    return success({"store_id": sid, "items": items}, request=request)


@router.put("/milestones", dependencies=[require_permission("coupon:manage")])
async def replace_milestones(
    data: MilestoneRewardsReplace,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """整表替换本店里程碑规则（空 items 表示清空）。"""
    sid = _resolve_milestone_store(user, data.store_id)
    items = await CouponService.replace_milestone_rewards(
        db,
        sid,
        [x.model_dump() for x in data.items],
    )
    await db.commit()
    await AuditService.log_staff(
        db,
        user=user,
        action="coupon.milestone.replace",
        module="coupon",
        target_type="store",
        target_id=sid,
        detail={"store_id": sid, "items": [x.model_dump() for x in data.items]},
        request=request,
        store_id=sid,
    )
    return success({"store_id": sid, "items": items}, request=request)


# ── 积分兑换选项管理 ──────────────────────────────────────────

class PointExchangeOptionItem(BaseModel):
    name: str = Field(..., max_length=100)
    template_id: int = Field(..., ge=1)
    points_cost: int = Field(..., ge=1)
    status: int = Field(default=1, ge=0, le=1)
    sort_order: int = Field(default=0, ge=0, le=9999)


class PointExchangeOptionReplace(BaseModel):
    store_id: int | None = Field(default=None, description="超管指定门店；门店账号忽略；不传=总部全局选项")
    items: list[PointExchangeOptionItem] = Field(default_factory=list)


@router.get("/point-exchange-options", dependencies=[require_permission("coupon:read")])
async def list_point_exchange_options(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    store_id: int | None = None,
):
    """积分兑换选项列表（门店账号仅看本店+总部，超管可指定门店）。"""
    from app.models import PointExchangeOption

    bound = require_bound_store(user, None)
    is_hq = user.role.code == "SUPER_ADMIN"
    if is_hq:
        sid = int(store_id) if store_id else None
    else:
        sid = bound

    q = select(PointExchangeOption).order_by(PointExchangeOption.sort_order, PointExchangeOption.id)
    if sid is not None:
        q = q.where(
            or_(PointExchangeOption.store_id.is_(None), PointExchangeOption.store_id == int(sid))
        )
    else:
        q = q.where(PointExchangeOption.store_id.is_(None))
    rows = list((await db.execute(q)).scalars().all())
    items = []
    for opt in rows:
        tmpl = await db.get(CouponTemplate, int(opt.template_id))
        items.append(
            {
                "id": opt.id,
                "store_id": int(opt.store_id) if opt.store_id else None,
                "name": opt.name,
                "template_id": int(opt.template_id),
                "template_name": tmpl.name if tmpl else None,
                "points_cost": int(opt.points_cost),
                "status": int(opt.status),
                "sort_order": int(opt.sort_order),
            }
        )
    return success({"store_id": sid, "items": items}, request=request)


@router.put("/point-exchange-options", dependencies=[require_permission("coupon:manage")])
async def replace_point_exchange_options(
    data: PointExchangeOptionReplace,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """整表替换本店（或总部）积分兑换选项（空 items 表示清空）。"""
    from app.models import PointExchangeOption

    bound = require_bound_store(user, None)
    is_hq = user.role.code == "SUPER_ADMIN"
    if is_hq:
        sid = int(data.store_id) if data.store_id else None
    else:
        sid = bound

    # 校验模板有效性
    for item in data.items:
        tmpl = await db.get(CouponTemplate, int(item.template_id))
        if not tmpl or int(tmpl.status) != 1:
            raise AppException(40404, f"券模板 {item.template_id} 不存在或已停用")

    # 删除旧项
    if sid is not None:
        await db.execute(
            PointExchangeOption.__table__.delete().where(
                PointExchangeOption.store_id == int(sid)
            )
        )
    else:
        await db.execute(
            PointExchangeOption.__table__.delete().where(
                PointExchangeOption.store_id.is_(None)
            )
        )
    # 写入新项
    for item in data.items:
        db.add(
            PointExchangeOption(
                store_id=int(sid) if sid else None,
                name=item.name,
                template_id=int(item.template_id),
                points_cost=int(item.points_cost),
                status=int(item.status),
                sort_order=int(item.sort_order),
            )
        )
    await db.commit()

    override_warnings: list[str] = []
    if sid is not None and data.items:
        hq_rows = list(
            (
                await db.execute(
                    select(PointExchangeOption).where(PointExchangeOption.store_id.is_(None))
                )
            ).scalars().all()
        )
        hq_keys = {(int(o.template_id), int(o.points_cost)): o for o in hq_rows}
        for item in data.items:
            key = (int(item.template_id), int(item.points_cost))
            hq = hq_keys.get(key)
            if hq:
                override_warnings.append(
                    hq_override_warning(
                        key_label="积分兑券档",
                        key_desc=f"模板#{item.template_id} / {item.points_cost}积分",
                        hq_name=hq.name,
                    )
                )

    await AuditService.log_staff(
        db,
        user=user,
        action="coupon.point_exchange.replace",
        module="coupon",
        target_type="store",
        target_id=int(sid) if sid else 0,
        detail={"store_id": sid, "items": [x.model_dump() for x in data.items]},
        request=request,
        store_id=int(sid) if sid else None,
    )
    payload: dict = {"store_id": sid, "items": [x.model_dump() for x in data.items]}
    if override_warnings:
        payload["override_warnings"] = override_warnings
        payload["override_warning"] = override_warnings[0]
    return success(payload, request=request)
