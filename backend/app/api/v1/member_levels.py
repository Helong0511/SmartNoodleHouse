from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Customer
from app.schemas.member_level import MemberLevelsBatchUpdate
from app.services.customer_service import CustomerService
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/member-levels", tags=["会员等级"])


@router.get("", dependencies=[require_permission("customer:read")])
async def list_member_levels(request: Request, db: DbSession, user: CurrentUser):
    """后台：会员等级列表（读库 member_levels，按积分升序）。人数按当前门店统计。"""
    levels = await CustomerService.list_levels_ordered(db)
    counts_q = (
        select(Customer.member_level_id, func.count())
        .where(Customer.status == 1)
        .group_by(Customer.member_level_id)
    )
    if user.role.code != "SUPER_ADMIN":
        from app.api.v1.customers import _customer_in_store

        sid = require_bound_store(user, None)
        if sid is not None:
            counts_q = counts_q.where(_customer_in_store(int(sid)))
    counts = {row[0]: int(row[1]) for row in (await db.execute(counts_q)).all()}
    return success(
        {
            "points_rule": "实付1元=1积分，积分达标自动升级享对应折扣",
            "items": [
                CustomerService.level_admin_payload(lv, counts.get(lv.id, 0)) for lv in levels
            ],
        },
        request=request,
    )


@router.put("", dependencies=[require_permission("customer:manage")])
async def update_member_levels(
    data: MemberLevelsBatchUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    """调整各等级升级积分与折扣。规则全品牌共用，仅超级管理员可改。"""
    if user.role.code != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="会员等级为全品牌规则，仅超级管理员可修改")
    try:
        result = await CustomerService.apply_level_updates(
            db, [item.model_dump() for item in data.items], recalculate=data.recalculate
        )
    except AppException:
        raise
    from app.services.audit_service import AuditService

    await AuditService.log_staff(
        db,
        user=user,
        action="member_level.update",
        module="customer",
        target_type="member_levels",
        detail={"changed": result["changed"], "recalculated": result["recalculated"]},
        request=request,
    )
    await db.flush()
    levels = await CustomerService.list_levels_ordered(db)
    counts_q = (
        select(Customer.member_level_id, func.count())
        .where(Customer.status == 1)
        .group_by(Customer.member_level_id)
    )
    counts = {row[0]: int(row[1]) for row in (await db.execute(counts_q)).all()}
    return success(
        {
            "changed": result["changed"],
            "recalculated": result["recalculated"],
            "items": [
                CustomerService.level_admin_payload(lv, counts.get(lv.id, 0)) for lv in levels
            ],
        },
        message="会员等级已保存；已按新积分门槛重算顾客等级"
        if data.recalculate
        else "会员等级已保存",
        request=request,
    )
