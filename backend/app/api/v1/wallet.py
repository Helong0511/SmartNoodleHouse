"""储值档位、充值下单/支付、余额查询与调账。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentCustomer, CurrentUser, DbSession, OptionalStaffUser, require_permission
from app.core.biz_no import generate_no
from app.core.exceptions import AppException
from app.core.response import calc_pages, money, success
from app.models import BalanceLedger, Customer, RechargeOrder, RechargePackage, Store
from app.services.audit_service import AuditService
from app.services.balance_service import BalanceService
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
    customer_id: int
    delta_principal: Decimal = Decimal("0")
    delta_gift: Decimal = Decimal("0")
    remark: str | None = Field(default=None, max_length=255)
    store_id: int | None = None


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
):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    effective = require_bound_store(user, store_id)
    q = select(BalanceLedger)
    if effective is not None:
        q = q.where(BalanceLedger.store_id == effective)
    if customer_id:
        q = q.where(BalanceLedger.customer_id == int(customer_id))
    if entry_type:
        q = q.where(BalanceLedger.entry_type == entry_type.strip().upper())
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(
        (
            await db.execute(
                q.order_by(BalanceLedger.id.desc()).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
    )
    items = [
        {
            "id": r.id,
            "customer_id": r.customer_id,
            "store_id": r.store_id,
            "order_id": r.order_id,
            "recharge_order_id": r.recharge_order_id,
            "payment_id": r.payment_id,
            "refund_id": r.refund_id,
            "entry_type": r.entry_type,
            "delta_principal": str(money(r.delta_principal)),
            "delta_gift": str(money(r.delta_gift)),
            "principal_after": str(money(r.principal_after)),
            "gift_after": str(money(r.gift_after)),
            "remark": r.remark,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
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


@router.post("/adjust", dependencies=[require_permission("wallet:manage")])
async def adjust_balance(data: AdjustBody, request: Request, db: DbSession, user: CurrentUser):
    sid = require_bound_store(user, data.store_id) if user.role.code != "SUPER_ADMIN" else data.store_id
    row = await BalanceService.adjust(
        db,
        customer_id=data.customer_id,
        delta_principal=data.delta_principal,
        delta_gift=data.delta_gift,
        store_id=sid,
        remark=data.remark,
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
        },
        request=request,
    )
    return success(
        {
            "id": row.id,
            "principal_after": str(money(row.principal_after)),
            "gift_after": str(money(row.gift_after)),
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
                return success({**_ro_ser(existed), "already_paid": True, "status": "PAID"}, request=request)
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
    return success(
        {
            **_ro_ser(ro),
            "payment_no": payment.payment_no,
            "amount": str(payment.amount),
            **pay_data,
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
