import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import calc_pages, success
from app.models import DiningTable, Store
from app.services.store_scope import assert_store_access, require_bound_store

router = APIRouter(prefix="/tables", tags=["桌台"])


class TableCreate(BaseModel):
    store_id: int | None = None
    table_no: str = Field(..., min_length=1, max_length=20)
    table_name: str | None = Field(default=None, max_length=50)


class TableUpdate(BaseModel):
    table_no: str | None = None
    table_name: str | None = None
    status: int | None = None


def _qr_urls(token: str) -> tuple[str, str]:
    """生成微信/支付宝小程序扫码路径（不可用 table_no 伪造）"""
    settings = get_settings()
    wx_appid = settings.wechat_app_id or "YOUR_WECHAT_APPID"
    ali_appid = settings.alipay_app_id or "YOUR_ALIPAY_APPID"
    wechat = f"weixin://dl/business/?appid={wx_appid}&path=pages/index/index&query=token%3D{token}%26channel%3Dwechat"
    alipay = f"alipays://platformapi/startapp?appId={ali_appid}&page=pages/index/index&query=token%3D{token}%26channel%3Dalipay"
    return wechat, alipay


def _serialize(t: DiningTable) -> dict:
    return {
        "id": t.id,
        "store_id": t.store_id,
        "table_no": t.table_no,
        "table_name": t.table_name,
        "qr_token": t.qr_token,
        "wechat_qr_url": t.wechat_qr_url,
        "alipay_qr_url": t.alipay_qr_url,
        "wechat_page": f"pages/index/index?token={t.qr_token}&channel=wechat",
        "alipay_page": f"pages/index/index?token={t.qr_token}&channel=alipay",
        "h5_fallback": f"/order?token={t.qr_token}",
        "status": t.status,
    }


async def _get_owned_table(db, user, table_id: int) -> DiningTable:
    table = await db.get(DiningTable, table_id)
    if not table:
        raise AppException(40430, "桌台不存在")
    assert_store_access(user, table.store_id)
    return table


@router.get("")
async def list_tables(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    status: int | None = None,
    store_id: int | None = None,
):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    effective = require_bound_store(user, store_id)
    q = select(DiningTable)
    if effective is not None:
        q = q.where(DiningTable.store_id == effective)
    if status is not None:
        q = q.where(DiningTable.status == status)
    total = await db.scalar(select(func.count()).select_from(q.subquery())) or 0
    result = await db.execute(
        q.order_by(DiningTable.store_id, DiningTable.table_no).offset((page - 1) * page_size).limit(page_size)
    )
    items = list(result.scalars().all())
    store_ids = list({t.store_id for t in items})
    store_names: dict[int, str] = {}
    if store_ids:
        rows = await db.execute(select(Store).where(Store.id.in_(store_ids)))
        store_names = {s.id: s.name for s in rows.scalars().all()}
    return success(
        {
            "items": [{**_serialize(t), "store_name": store_names.get(t.store_id)} for t in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total, page_size),
            "effective_store_id": effective,
        },
        request=request,
    )


@router.post("", dependencies=[require_permission("table:manage")])
async def create_table(
    data: TableCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    # 门店账号强制本店；超管必须显式指定或使用已绑店，禁止静默写店 1
    if user.role.code == "SUPER_ADMIN":
        sid = int(data.store_id) if data.store_id else (int(user.store_id) if user.store_id else 0)
        if not sid:
            raise AppException(40060, "请选择门店后再创建桌台")
        assert_store_access(user, sid)
    else:
        sid = require_bound_store(user, data.store_id)
        if sid is None:
            raise AppException(40060, "账号未绑定门店")

    store = await db.get(Store, sid)
    if not store or store.status != 1:
        raise AppException(40061, "门店不存在或已停用")

    table_no = data.table_no.strip()
    exists = await db.scalar(
        select(func.count()).select_from(DiningTable).where(
            DiningTable.store_id == sid, DiningTable.table_no == table_no
        )
    )
    if exists:
        raise AppException(40062, f"本店桌号「{table_no}」已存在")

    token = secrets.token_urlsafe(32)
    wechat_url, alipay_url = _qr_urls(token)
    table = DiningTable(
        store_id=sid,
        table_no=table_no,
        table_name=(data.table_name or table_no).strip() or table_no,
        qr_token=token,
        wechat_qr_url=wechat_url,
        alipay_qr_url=alipay_url,
    )
    db.add(table)
    await db.flush()
    return success(_serialize(table), request=request)


@router.put("/{table_id}", dependencies=[require_permission("table:manage")])
async def update_table(
    table_id: int,
    data: TableUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
):
    table = await _get_owned_table(db, user, table_id)
    payload = data.model_dump(exclude_unset=True)
    if "table_no" in payload and payload["table_no"]:
        new_no = str(payload["table_no"]).strip()
        clash = await db.scalar(
            select(func.count()).select_from(DiningTable).where(
                DiningTable.store_id == table.store_id,
                DiningTable.table_no == new_no,
                DiningTable.id != table.id,
            )
        )
        if clash:
            raise AppException(40062, f"本店桌号「{new_no}」已存在")
        payload["table_no"] = new_no
    for k, v in payload.items():
        setattr(table, k, v)
    await db.flush()
    return success(_serialize(table), request=request)


@router.get("/resolve/{qr_token}")
async def resolve_table(qr_token: str, request: Request, db: DbSession):
    result = await db.execute(
        select(DiningTable).where(DiningTable.qr_token == qr_token, DiningTable.status == 1)
    )
    table = result.scalar_one_or_none()
    if not table:
        return success(None, message="无效桌台", request=request)
    return success(
        {
            "id": table.id,
            "table_no": table.table_no,
            "table_name": table.table_name,
            "store_id": table.store_id,
        },
        request=request,
    )


@router.post("/{table_id}/regenerate-qr", dependencies=[require_permission("table:manage")])
async def regenerate_qr(table_id: int, request: Request, db: DbSession, user: CurrentUser):
    table = await _get_owned_table(db, user, table_id)
    token = secrets.token_urlsafe(32)
    wechat_url, alipay_url = _qr_urls(token)
    table.qr_token = token
    table.wechat_qr_url = wechat_url
    table.alipay_qr_url = alipay_url
    await db.flush()
    return success(_serialize(table), request=request)
