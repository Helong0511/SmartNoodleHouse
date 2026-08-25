import secrets

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.config import get_settings
from app.core.response import calc_pages, success
from app.models import DiningTable

router = APIRouter(prefix="/tables", tags=["桌台"])


def _qr_urls(token: str) -> tuple[str, str]:
    """生成微信/支付宝小程序扫码路径（不可用 table_no 伪造）"""
    settings = get_settings()
    wx_appid = settings.wechat_app_id or "YOUR_WECHAT_APPID"
    ali_appid = settings.alipay_app_id or "YOUR_ALIPAY_APPID"
    # 小程序码落地页；也兼容 H5 兜底
    wechat = f"weixin://dl/business/?appid={wx_appid}&path=pages/index/index&query=token%3D{token}%26channel%3Dwechat"
    alipay = f"alipays://platformapi/startapp?appId={ali_appid}&page=pages/index/index&query=token%3D{token}%26channel%3Dalipay"
    return wechat, alipay


@router.get("")
async def list_tables(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    page: int = 1,
    page_size: int = 20,
    status: int | None = None,
):
    q = select(DiningTable)
    if status is not None:
        q = q.where(DiningTable.status == status)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    result = await db.execute(q.offset((page - 1) * page_size).limit(page_size).order_by(DiningTable.id))
    items = result.scalars().all()
    return success(
        {
            "items": [
                {
                    "id": t.id,
                    "store_id": t.store_id,
                    "table_no": t.table_no,
                    "table_name": t.table_name,
                    "qr_token": t.qr_token,
                    "wechat_qr_url": t.wechat_qr_url,
                    "alipay_qr_url": t.alipay_qr_url,
                    "status": t.status,
                }
                for t in items
            ],
            "total": total or 0,
            "page": page,
            "page_size": page_size,
            "total_pages": calc_pages(total or 0, page_size),
        },
        request=request,
    )


@router.post("", dependencies=[require_permission("system:manage")])
async def create_table(
    request: Request,
    db: DbSession,
    store_id: int = 1,
    table_no: str = "",
    table_name: str = "",
):
    token = secrets.token_urlsafe(32)
    wechat_url, alipay_url = _qr_urls(token)
    table = DiningTable(
        store_id=store_id,
        table_no=table_no,
        table_name=table_name or table_no,
        qr_token=token,
        wechat_qr_url=wechat_url,
        alipay_qr_url=alipay_url,
    )
    db.add(table)
    await db.flush()
    return success(
        {
            "id": table.id,
            "qr_token": token,
            "wechat_qr_url": wechat_url,
            "alipay_qr_url": alipay_url,
            "wechat_page": f"pages/index/index?token={token}&channel=wechat",
            "alipay_page": f"pages/index/index?token={token}&channel=alipay",
            "h5_fallback": f"/order?token={token}",
        },
        request=request,
    )


@router.get("/resolve/{qr_token}")
async def resolve_table(qr_token: str, request: Request, db: DbSession):
    result = await db.execute(
        select(DiningTable).where(DiningTable.qr_token == qr_token, DiningTable.status == 1)
    )
    table = result.scalar_one_or_none()
    if not table:
        return success(None, message="无效桌台", request=request)
    return success(
        {"id": table.id, "table_no": table.table_no, "table_name": table.table_name, "store_id": table.store_id},
        request=request,
    )


@router.post("/{table_id}/regenerate-qr", dependencies=[require_permission("system:manage")])
async def regenerate_qr(table_id: int, request: Request, db: DbSession):
    table = await db.get(DiningTable, table_id)
    if not table:
        return success(None, message="桌台不存在", request=request)
    token = secrets.token_urlsafe(32)
    wechat_url, alipay_url = _qr_urls(token)
    table.qr_token = token
    table.wechat_qr_url = wechat_url
    table.alipay_qr_url = alipay_url
    await db.flush()
    return success(
        {
            "qr_token": token,
            "wechat_qr_url": wechat_url,
            "alipay_qr_url": alipay_url,
            "wechat_page": f"pages/index/index?token={token}&channel=wechat",
            "alipay_page": f"pages/index/index?token={token}&channel=alipay",
        },
        request=request,
    )
