"""操作审计日志查询接口：按分类/模块/用户/时间/门店/关键字筛选。

可见范围：
- SUPER_ADMIN：全库可见
- STORE_ADMIN：本店可见 + 自己操作的记录
- CASHIER/KITCHEN/其他：仅自己操作的记录（且本店）
- 任何角色：DANGER/INTRUSION 分类仅超管可见（防止恶意用户探查自身告警边界）
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.response import calc_pages, success, serialize_dt
from app.models import OperationLog, User
from app.services.store_scope import require_bound_store

router = APIRouter(prefix="/audit-logs", tags=["操作日志"])


@router.get("", dependencies=[require_permission("audit:read")])
async def list_audit_logs(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    store_id: int | None = None,
    category: str | None = Query(None, description="STAFF/STORE_ADMIN/CASHIER/KITCHEN/SYSTEM/CUSTOMER/DANGER/INTRUSION"),
    module: str | None = None,
    action: str | None = None,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    keyword: str | None = Query(None, description="按 action/detail 模糊匹配"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    is_super = user.role.code == "SUPER_ADMIN"
    sid = require_bound_store(user, store_id)

    q = select(OperationLog)
    cq = select(func.count()).select_from(OperationLog)

    # 可见范围控制
    if is_super:
        if sid is not None:
            # 超管明确选了某门店：严格只看该门店；
            # store_id=NULL 的系统级/超管操作不混入（不选门店时才看全部）
            q = q.where(OperationLog.store_id == sid)
            cq = cq.where(OperationLog.store_id == sid)
    else:
        # 非超管不能看 DANGER/INTRUSION：防止恶意用户探查告警边界
        not_danger = ~OperationLog.category.in_(["DANGER", "INTRUSION"])
        uid = int(user.id)
        if sid is None:
            # 非超管必须绑店（require_bound_store 已对未绑店账号抛 403）；兜底仅本人
            scope = and_(OperationLog.user_id == uid, not_danger)
        elif user.role.code == "STORE_ADMIN":
            # 店长：本店全部操作记录 + 本人记录（含本人 store_id 为空的系统级操作）
            scope = and_(
                or_(OperationLog.store_id == sid, OperationLog.user_id == uid),
                not_danger,
            )
        else:
            # 收银员/厨房/其他：严格限定本店 + 本人，禁止查看本店其他员工记录
            scope = and_(
                OperationLog.store_id == sid,
                OperationLog.user_id == uid,
                not_danger,
            )
        q = q.where(scope)
        cq = cq.where(scope)

    if category:
        cat = category.strip().upper()
        q = q.where(OperationLog.category == cat)
        cq = cq.where(OperationLog.category == cat)
    if module:
        mod = module.strip().lower()
        q = q.where(OperationLog.module == mod)
        cq = cq.where(OperationLog.module == mod)
    if action:
        act = action.strip().lower()
        q = q.where(OperationLog.action == act)
        cq = cq.where(OperationLog.action == act)
    if user_id:
        q = q.where(OperationLog.user_id == int(user_id))
        cq = cq.where(OperationLog.user_id == int(user_id))
    if target_type:
        tt = target_type.strip().lower()
        q = q.where(OperationLog.target_type == tt)
        cq = cq.where(OperationLog.target_type == tt)
    if target_id:
        q = q.where(OperationLog.target_id == int(target_id))
        cq = cq.where(OperationLog.target_id == int(target_id))
    if keyword:
        # 转义 LIKE 通配符，防止用户输入 %/_ 触发全表模糊扫描
        raw = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        kw = f"%{raw}%"
        q = q.where(or_(OperationLog.action.like(kw, escape="\\"), OperationLog.detail.like(kw, escape="\\")))
        cq = cq.where(or_(OperationLog.action.like(kw, escape="\\"), OperationLog.detail.like(kw, escape="\\")))
    from app.core.response import apply_datetime_range

    q = apply_datetime_range(q, OperationLog.created_at, date_from, date_to)
    cq = apply_datetime_range(cq, OperationLog.created_at, date_from, date_to)

    total = int((await db.execute(cq)).scalar() or 0)
    # 排序加 id 兜底，避免翻页重复/丢行
    rows = (
        await db.execute(
            q.order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    # 批量补 user 信息（避免 N+1）
    user_ids = list({r.user_id for r in rows if r.user_id})
    user_map: dict[int, dict] = {}
    if user_ids:
        urows = await db.execute(
            select(User.id, User.username, User.display_name, User.store_id).where(User.id.in_(user_ids))
        )
        for uid, uname, dname, ustore in urows.all():
            user_map[uid] = {
                "username": uname,
                "display_name": dname,
                "store_id": ustore,
            }

    items = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "username": user_map.get(r.user_id, {}).get("username") if r.user_id else None,
            "display_name": user_map.get(r.user_id, {}).get("display_name") if r.user_id else None,
            "action": r.action,
            "module": r.module,
            "category": r.category,
            "store_id": r.store_id,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "ip": r.ip,
            "user_agent": r.user_agent,
            "request_id": r.request_id,
            "detail": r.detail,
            "created_at": serialize_dt(r.created_at),
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
            "is_super_admin": is_super,
        },
        request=request,
    )


@router.get("/categories", dependencies=[require_permission("audit:read")])
async def list_audit_categories(request: Request, db: DbSession, user: CurrentUser):
    """返回所有分类常量及中文标签，供前端筛选下拉。"""
    cats = [
        {"value": "STAFF", "label": "员工通用"},
        {"value": "STORE_ADMIN", "label": "店长"},
        {"value": "CASHIER", "label": "收银员"},
        {"value": "KITCHEN", "label": "厨房"},
        {"value": "SYSTEM", "label": "系统"},
        {"value": "CUSTOMER", "label": "顾客"},
        {"value": "DANGER", "label": "危险"},
        {"value": "INTRUSION", "label": "破解/渗透"},
    ]
    return success(
        {
            "items": cats,
            # 非超管不能筛选 DANGER/INTRUSION
            "visible_values": (
                [c["value"] for c in cats]
                if user.role.code == "SUPER_ADMIN"
                else [c["value"] for c in cats if c["value"] not in ("DANGER", "INTRUSION")]
            ),
        },
        request=request,
    )
