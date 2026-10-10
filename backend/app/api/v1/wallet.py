"""储值档位、充值下单/支付、余额查询与调账。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, OptionalStaffUser, require_permission
from app.core.biz_no import generate_no
from app.core.exceptions import AppException
from app.core.response import calc_pages, money, success
from app.models import BalanceLedger, Customer, Order, RechargeOrder, RechargePackage, Store, User
from app.services.audit_service import AuditService
from app.services.balance_service import BalanceService
from app.services.customer_service import CustomerService
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/wallet", tags=["储值"])


class PackageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    pay_amount: Decimal = Field(..., gt=0)
    gift_amount: Decimal = Field(default=Decimal("0"), ge=0)
    sort_order: int = 0
    status: int = 1
    store_id: int | None = None


class PackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    pay_amount: Decimal | None = Field(default=None, gt=0)
    gift_amount: Decimal | None = Field(default=None, ge=0)
    sort_order: int | None = None
    status: int | None = None


class RechargeCreate(BaseModel):
    package_id: int
    store_id: int | None = None
    provider: str = Field(description="WECHAT or ALIPAY")
    channel_type: str = "miniprogram"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)


class AdjustBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    delta_principal: Decimal = Field(default=Decimal("0"))
    delta_gift: Decimal = Field(default=Decimal("0"))
    remark: str | None = Field(default=None, max_length=255)
    store_id: int | None = None

    @model_validator(mode="after")
    def _normalize_and_reject_noop(self):
        from app.core.response import money

        self.delta_principal = money(self.delta_principal)
        self.delta_gift = money(self.delta_gift)
        if self.delta_principal == 0 and self.delta_gift == 0:
            raise ValueError("调账金额不能全为 0")
        # 单次调账绝对值上限，防止误传超大金额
        limit = Decimal("100000.00")
        if abs(self.delta_principal) > limit or abs(self.delta_gift) > limit:
            raise ValueError(f"单次调账金额不能超过 {limit}")
        return self


def _pkg_ser(p: RechargePackage, store_name: str | None = None) -> dict:
    credit = money(p.pay_amount) + money(p.gift_amount)
    return {
        "id": p.id,
        "store_id": p.store_id,
        "store_scope": "HQ" if not p.store_id else "STORE",
        "store_name": store_name,
        "name": p.name,
        "pay_amount": str(money(p.pay_amount)),
        "gift_amount": str(money(p.gift_amount)),
        "credit_total": str(credit),
        "sort_order": p.sort_order,
        "status": p.status,
    }


async def _assert_package_unique(
    db,
    *,
    store_id: int | None,
    name: str,
    pay_amount,
    exclude_id: int | None = None,
) -> None:
    """同范围（总部或同一门店）下：名称、实付金额均不可重复（含停用档）。"""
    name = (name or "").strip()
    pay = money(pay_amount)

    def _scope(q):
        if store_id is None:
            return q.where(RechargePackage.store_id.is_(None))
        return q.where(RechargePackage.store_id == int(store_id))

    q_name = _scope(select(RechargePackage).where(RechargePackage.name == name))
    if exclude_id:
        q_name = q_name.where(RechargePackage.id != int(exclude_id))
    if (await db.execute(q_name.limit(1))).scalar_one_or_none():
        scope = "总部" if store_id is None else f"门店#{store_id}"
        raise AppException(40093, f"{scope}已存在同名档位「{name}」")

    q_pay = _scope(select(RechargePackage).where(RechargePackage.pay_amount == pay))
    if exclude_id:
        q_pay = q_pay.where(RechargePackage.id != int(exclude_id))
    if (await db.execute(q_pay.limit(1))).scalar_one_or_none():
        scope = "总部" if store_id is None else f"门店#{store_id}"
        raise AppException(40094, f"{scope}已存在实付 ¥{pay} 的档位，不可重复")


def _ro_ser(r: RechargeOrder) -> dict:
    return {
        "id": r.id,
        "recharge_no": r.recharge_no,
        "customer_id": r.customer_id,
        "store_id": r.store_id,
        "package_id": r.package_id,
        "pay_amount": str(money(r.pay_amount)),
        "credit_principal": str(money(r.credit_principal)),
        "credit_gift": str(money(r.credit_gift)),
        "status": r.status,
        "channel": r.channel,
        "payment_id": r.payment_id,
        "paid_at": r.paid_at.isoformat() if r.paid_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/packages")
async def list_packages(
    request: Request,
    db: DbSession,
    staff: OptionalStaffUser,
    store_id: int | None = None,
    include_disabled: bool = False,
):
    """公开可读启用档位；员工后台可看全部/停用档位。"""
    q = select(RechargePackage)
    if staff:
        # 后台：超管看全部；门店账号看总部+本店
        if staff.role.code != "SUPER_ADMIN":
            bound = require_bound_store(staff, store_id)
            if bound is not None:
                q = q.where(or_(RechargePackage.store_id.is_(None), RechargePackage.store_id == int(bound)))
            elif store_id:
                q = q.where(or_(RechargePackage.store_id.is_(None), RechargePackage.store_id == int(store_id)))
        elif store_id:
            q = q.where(or_(RechargePackage.store_id.is_(None), RechargePackage.store_id == int(store_id)))
    elif store_id:
        q = q.where(or_(RechargePackage.store_id.is_(None), RechargePackage.store_id == int(store_id)))
    else:
        # 顾客端未传门店：仅总部档位
        q = q.where(RechargePackage.store_id.is_(None))
    if include_disabled:
        if not staff:
            raise AppException(40316, "查看停用档位需要登录员工账号")
    else:
        q = q.where(RechargePackage.status == 1)
    rows = list((await db.execute(q.order_by(RechargePackage.sort_order, RechargePackage.id))).scalars())
    store_ids = [int(p.store_id) for p in rows if p.store_id]
    name_map: dict[int, str] = {}
    if store_ids:
        for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars():
            name_map[int(s.id)] = s.name
    return success(
        [_pkg_ser(p, name_map.get(int(p.store_id)) if p.store_id else None) for p in rows],
        request=request,
    )


@router.post("/packages", dependencies=[require_permission("wallet:manage")])
async def create_package(data: PackageCreate, request: Request, db: DbSession, user: CurrentUser):
    sid = data.store_id
    if user.role.code != "SUPER_ADMIN":
        sid = require_bound_store(user, sid)
        if sid is None:
            raise AppException(40082, "账号未绑定门店")
    elif sid:
        assert_store_access(user, sid)
    name = data.name.strip()
    pay_amount = money(data.pay_amount)
    gift_amount = money(data.gift_amount)
    await _assert_package_unique(db, store_id=sid, name=name, pay_amount=pay_amount)
    pkg = RechargePackage(
        store_id=sid,
        name=name,
        pay_amount=pay_amount,
        gift_amount=gift_amount,
        sort_order=int(data.sort_order or 0),
        status=1 if data.status is None else int(data.status),
    )
    db.add(pkg)
    try:
        await db.flush()
    except IntegrityError as exc:
        msg = str(getattr(exc, "orig", None) or exc).lower()
        if "uk_rp_scope_pay" in msg:
            raise AppException(40094, "同范围实付金额档位已存在") from exc
        raise AppException(40093, "同范围档位名称已存在") from exc
    await AuditService.log(
        db,
        user_id=user.id,
        action="wallet.package.create",
        module="wallet",
        target_type="recharge_package",
        target_id=pkg.id,
        detail=_pkg_ser(pkg),
        request=request,
    )
    return success(_pkg_ser(pkg), request=request)


@router.put("/packages/{package_id}", dependencies=[require_permission("wallet:manage")])
async def update_package(
    package_id: int, data: PackageUpdate, request: Request, db: DbSession, user: CurrentUser
):
    pkg = await db.get(RechargePackage, package_id)
    if not pkg:
        raise AppException(40490, "档位不存在")
    if pkg.store_id:
        assert_store_access(user, pkg.store_id)
    elif user.role.code != "SUPER_ADMIN":
        raise AppException(40316, "仅超管可改总部档位")
    if data.name is not None:
        pkg.name = data.name.strip()
    if data.pay_amount is not None:
        pkg.pay_amount = money(data.pay_amount)
    if data.gift_amount is not None:
        pkg.gift_amount = money(data.gift_amount)
    if data.sort_order is not None:
        pkg.sort_order = int(data.sort_order)
    if data.status is not None:
        pkg.status = int(data.status)
    await _assert_package_unique(
        db,
        store_id=pkg.store_id,
        name=pkg.name,
        pay_amount=pkg.pay_amount,
        exclude_id=pkg.id,
    )
    try:
        await db.flush()
    except IntegrityError as exc:
        msg = str(getattr(exc, "orig", None) or exc).lower()
        if "uk_rp_scope_pay" in msg:
            raise AppException(40094, "同范围实付金额档位已存在") from exc
        raise AppException(40093, "同范围档位名称已存在") from exc
    await AuditService.log(
        db,
        user_id=user.id,
        action="wallet.package.update",
        module="wallet",
        target_type="recharge_package",
        target_id=pkg.id,
        detail=_pkg_ser(pkg),
        request=request,
    )
    return success(_pkg_ser(pkg), request=request)


@router.get("/me")
async def my_wallet(request: Request, db: DbSession, customer: CurrentCustomer):
    cust = await db.get(Customer, customer.id)
    return success(BalanceService.snapshot(cust) if cust else {}, request=request)


@router.get("/ledger", dependencies=[require_permission("wallet:read")])
async def admin_ledger(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    customer_id: int | None = None,
    store_id: int | None = None,
    entry_type: str | None = None,
    keyword: str | None = None,
):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    effective = require_bound_store(user, store_id)
    filters = []
    if effective is not None:
        # P0：严格按门店隔离。历史 store_id=NULL 的 ADJUST 不得跨店可见。
        filters.append(BalanceLedger.store_id == effective)
    elif user.role.code != "SUPER_ADMIN":
        raise AppException(40301, "账号未绑定门店")
    if customer_id:
        filters.append(BalanceLedger.customer_id == int(customer_id))
    if entry_type and entry_type.strip():
        filters.append(BalanceLedger.entry_type == entry_type.strip().upper())
    kw = (keyword or "").strip()
    if kw:
        cond = CustomerService.keyword_condition(kw)
        if cond is not None:
            filters.append(
                BalanceLedger.customer_id.in_(select(Customer.id).where(cond))
            )
    count_q = select(func.count()).select_from(BalanceLedger)
    list_q = select(BalanceLedger)
    for f in filters:
        count_q = count_q.where(f)
        list_q = list_q.where(f)
    total = await db.scalar(count_q) or 0
    rows = list(
        (
            await db.execute(
                list_q.order_by(BalanceLedger.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    cust_ids = list({r.customer_id for r in rows if r.customer_id})
    cust_map: dict[int, Customer] = {}
    if cust_ids:
        cust_map = {
            c.id: c
            for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars().all()
        }
    order_ids = list({r.order_id for r in rows if r.order_id})
    order_no_map: dict[int, str] = {}
    if order_ids:
        for oid, ono in (
            await db.execute(select(Order.id, Order.order_no).where(Order.id.in_(order_ids)))
        ).all():
            order_no_map[int(oid)] = ono
    recharge_ids = list({r.recharge_order_id for r in rows if r.recharge_order_id})
    recharge_no_map: dict[int, str] = {}
    if recharge_ids:
        for rid, rno in (
            await db.execute(
                select(RechargeOrder.id, RechargeOrder.recharge_no).where(RechargeOrder.id.in_(recharge_ids))
            )
        ).all():
            recharge_no_map[int(rid)] = rno
    op_ids = list({r.operator_id for r in rows if getattr(r, "operator_id", None)})
    op_map: dict[int, str] = {}
    if op_ids:
        for u in (await db.execute(select(User).where(User.id.in_(op_ids)))).scalars().all():
            op_map[int(u.id)] = u.display_name or u.username
    items = []
    for r in rows:
        cust = cust_map.get(r.customer_id)
        order_no = order_no_map.get(int(r.order_id)) if r.order_id else None
        recharge_no = recharge_no_map.get(int(r.recharge_order_id)) if r.recharge_order_id else None
        op_id = getattr(r, "operator_id", None)
        biz_no = getattr(r, "biz_no", None)
        entry = str(r.entry_type or "").upper()
        # 充值/消费/退款回补：操作人=顾客本人；调账等：员工
        if op_id:
            operator_kind = "STAFF"
            operator_name = op_map.get(int(op_id))
        elif entry in ("TOPUP", "PAY", "REFUND_RESTORE"):
            operator_kind = "CUSTOMER"
            cname = (cust.display_name or cust.nickname) if cust else None
            operator_name = f"{cname}（顾客本人）" if cname else "顾客本人"
        else:
            operator_kind = None
            operator_name = None
        ref_no = order_no or recharge_no or biz_no
        if order_no:
            ref_kind = "ORDER"
        elif recharge_no:
            ref_kind = "RECHARGE"
        elif biz_no:
            ref_kind = "ADJUST" if entry == "ADJUST" else "BIZ"
        else:
            ref_kind = None
        items.append(
            {
                "id": r.id,
                "customer_id": r.customer_id,
                "customer_no": cust.customer_no if cust else None,
                "customer_name": (cust.display_name or cust.nickname) if cust else None,
                "customer_nickname": cust.nickname if cust else None,
                "customer_phone": cust.phone if cust else None,
                "customer_avatar_url": cust.avatar_url if cust else None,
                "store_id": r.store_id,
                "order_id": r.order_id,
                "order_no": order_no,
                "recharge_order_id": r.recharge_order_id,
                "recharge_no": recharge_no,
                "biz_no": biz_no,
                "ref_no": ref_no,
                "ref_kind": ref_kind,
                "payment_id": r.payment_id,
                "refund_id": r.refund_id,
                "operator_id": int(op_id) if op_id else None,
                "operator_kind": operator_kind,
                "operator_name": operator_name,
                "entry_type": r.entry_type,
                "delta_principal": str(money(r.delta_principal)),
                "delta_gift": str(money(r.delta_gift)),
                "principal_after": str(money(r.principal_after)),
                "gift_after": str(money(r.gift_after)),
                "remark": r.remark,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
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


@router.get("/customers/search", dependencies=[require_permission("wallet:read")])
async def search_customers_for_wallet(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    keyword: str = Query(..., min_length=1, max_length=100, description="昵称/手机/顾客编号"),
    page_size: int = 20,
):
    """调账/流水筛选：按门店隔离搜索顾客。超管可搜全库。"""
    store_id = require_bound_store(user, None)
    rows, _total = await CustomerService.search_active_customers(
        db, keyword, page=1, page_size=page_size, store_id=store_id
    )
    items = [
        {
            "id": c.id,
            "customer_no": c.customer_no,
            "display_name": c.display_name or c.nickname,
            "nickname": c.nickname,
            "phone": c.phone,
            "avatar_url": c.avatar_url,
            **BalanceService.snapshot(c),
        }
        for c in rows
    ]
    return success({"items": items}, request=request)


@router.post("/adjust", dependencies=[require_permission("wallet:manage")])
async def adjust_balance(data: AdjustBody, request: Request, db: DbSession, user: CurrentUser):
    # P0：调账必须落门店，禁止 store_id=NULL 的跨店 ADJUST 流水
    if user.role.code == "SUPER_ADMIN":
        sid = data.store_id
        if not sid:
            raise AppException(40051, "调账必须指定门店")
    else:
        sid = require_bound_store(user, data.store_id)
        if not sid:
            raise AppException(40301, "账号未绑定门店")
        from app.api.v1.customers import _assert_customer_in_store

        await _assert_customer_in_store(db, data.customer_id, int(sid), action="调账")
    row = await BalanceService.adjust(
        db,
        customer_id=data.customer_id,
        delta_principal=data.delta_principal,
        delta_gift=data.delta_gift,
        store_id=int(sid),
        remark=data.remark,
        operator_id=user.id,
    )
    await AuditService.log(
        db,
        user_id=user.id,
        action="wallet.adjust",
        module="wallet",
        target_type="customer",
        target_id=data.customer_id,
        detail={
            "delta_principal": str(money(data.delta_principal)),
            "delta_gift": str(money(data.delta_gift)),
            "remark": data.remark,
            "operator_id": user.id,
            "biz_no": getattr(row, "biz_no", None),
        },
        request=request,
    )
    return success(
        {
            "id": row.id,
            "biz_no": getattr(row, "biz_no", None),
            "principal_after": str(money(row.principal_after)),
            "gift_after": str(money(row.gift_after)),
            "operator_id": user.id,
        },
        request=request,
    )


@router.post("/recharge")
async def create_recharge(
    data: RechargeCreate,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """顾客选档充值并拉起微信/支付宝支付。"""
    from app.services.payment_service import PaymentService

    if not CustomerService.is_member(customer):
        raise AppException(40320, "请先开通会员后再充值", status_code=403)

    pkg = await db.get(RechargePackage, data.package_id)
    if not pkg or int(pkg.status) != 1:
        raise AppException(40490, "充值档位不可用")
    store_id = int(data.store_id) if data.store_id else None
    if not store_id:
        raise AppException(40061, "请选择充值门店")
    store = await db.get(Store, store_id)
    if not store or store.status != 1:
        raise AppException(40061, "门店不存在或已停用")
    if pkg.store_id and int(pkg.store_id) != store_id:
        raise AppException(40092, "该档位不适用于当前门店")

    ro = None
    if data.idempotency_key:
        existed = (
            await db.execute(
                select(RechargeOrder).where(RechargeOrder.idempotency_key == data.idempotency_key).limit(1)
            )
        ).scalar_one_or_none()
        if existed:
            if int(existed.customer_id) != int(customer.id):
                raise AppException(40909, "幂等键冲突", status_code=409)
            if existed.status == "PAID":
                cust = await db.get(Customer, customer.id)
                return success(
                    {
                        **_ro_ser(existed),
                        "already_paid": True,
                        "status": "PAID",
                        "wallet": BalanceService.snapshot(cust) if cust else {},
                    },
                    request=request,
                )
            ro = existed

    if ro is None:
        sno = store.store_no or f"S{store.id}"
        ro = RechargeOrder(
            recharge_no=generate_no("RC", sno, "00"),
            customer_id=customer.id,
            store_id=store_id,
            package_id=pkg.id,
            pay_amount=money(pkg.pay_amount),
            credit_principal=money(pkg.pay_amount),
            credit_gift=money(pkg.gift_amount),
            status="PENDING",
            channel=data.provider.upper(),
            idempotency_key=data.idempotency_key,
        )
        db.add(ro)
        await db.flush()

    provider_u = data.provider.upper()
    if provider_u not in ("WECHAT", "ALIPAY"):
        raise AppException(40041, "充值仅支持微信或支付宝")

    extra: dict | None = None
    channel_l = data.channel_type.lower()
    if provider_u == "WECHAT" and channel_l == "miniprogram":
        from app.api.v1.payments import _wechat_openid_for_customer

        openid = await _wechat_openid_for_customer(db, customer.id)
        if openid:
            extra = {"openid": openid}
    elif provider_u == "ALIPAY" and channel_l == "miniprogram":
        from app.api.v1.payments import _alipay_buyer_id_for_customer

        buyer_id = await _alipay_buyer_id_for_customer(db, customer.id)
        if buyer_id:
            extra = {"buyer_id": buyer_id}

    payment, pay_data = await PaymentService.create_recharge_payment(
        db,
        recharge=ro,
        provider=provider_u,
        channel_type=data.channel_type,
        customer_id=customer.id,
        extra=extra,
    )
    await db.refresh(ro)
    cust = await db.get(Customer, customer.id)
    wallet = BalanceService.snapshot(cust) if cust else {}
    out = {
        **_ro_ser(ro),
        "payment_no": payment.payment_no,
        "amount": str(payment.amount),
        **pay_data,
        "wallet": wallet,
    }
    return success(out, request=request)


@router.get("/recharge/mine")
async def my_recharge_orders(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
):
    """顾客端：我的充值记录。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 50)
    q = select(RechargeOrder).where(RechargeOrder.customer_id == int(customer.id))
    if status and status.strip():
        parts = [p.strip().upper() for p in status.split(",") if p.strip()]
        if len(parts) == 1:
            q = q.where(RechargeOrder.status == parts[0])
        elif parts:
            q = q.where(RechargeOrder.status.in_(parts))
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(RechargeOrder.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    store_ids = list({r.store_id for r in rows if r.store_id})
    store_map: dict[int, str] = {}
    if store_ids:
        for sid, sname in (await db.execute(select(Store.id, Store.name).where(Store.id.in_(store_ids)))).all():
            store_map[int(sid)] = sname
    pkg_ids = list({r.package_id for r in rows if r.package_id})
    pkg_map: dict[int, str] = {}
    if pkg_ids:
        for pid, pname in (
            await db.execute(select(RechargePackage.id, RechargePackage.name).where(RechargePackage.id.in_(pkg_ids)))
        ).all():
            pkg_map[int(pid)] = pname
    status_labels = {
        "PENDING": "待支付",
        "PAID": "已到账",
        "CANCELLED": "已取消",
        "EXPIRED": "已过期",
        "FAILED": "失败",
    }
    channel_labels = {"WECHAT": "微信", "ALIPAY": "支付宝"}
    items = []
    for r in rows:
        credit_total = money(r.credit_principal) + money(r.credit_gift)
        items.append(
            {
                **_ro_ser(r),
                "package_name": pkg_map.get(int(r.package_id)) if r.package_id else None,
                "store_name": store_map.get(int(r.store_id)) if r.store_id else None,
                "credit_total": str(credit_total),
                "status_label": status_labels.get(str(r.status or "").upper(), r.status),
                "channel_label": channel_labels.get(str(r.channel or "").upper(), r.channel),
            }
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


@router.post("/recharge/{recharge_id}/sync")
async def sync_recharge(
    recharge_id: int,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    from app.services.payment_service import PaymentService

    ro = await db.get(RechargeOrder, recharge_id)
    if not ro or int(ro.customer_id) != int(customer.id):
        raise AppException(40491, "充值单不存在")
    result = await PaymentService.sync_recharge_payment(db, ro)
    await db.refresh(ro)
    cust = await db.get(Customer, customer.id)
    return success(
        {**_ro_ser(ro), **(result or {}), "wallet": BalanceService.snapshot(cust) if cust else {}},
        request=request,
    )
