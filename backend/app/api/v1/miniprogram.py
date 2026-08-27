"""小程序登录与扫码点餐入口"""
import base64

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, File, Request, UploadFile

from app.api.deps import CurrentCustomer, DbSession
from app.core.security import create_customer_token
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import Customer, DiningTable, FaceProfile
from app.services.customer_service import CustomerService
from app.services.face_extractor import extract_face_embedding_with_quality
from app.services.face_service import FaceService
from app.services.miniprogram_auth import AlipayMiniAuth, WechatMiniAuth

router = APIRouter(prefix="/miniprogram", tags=["小程序"])


class WechatMiniLoginRequest(BaseModel):
    code: str = Field(..., description="wx.login 返回的 code")
    nickname: str | None = None
    avatar_url: str | None = None
    table_token: str | None = None


class AlipayMiniLoginRequest(BaseModel):
    auth_code: str = Field(..., description="my.getAuthCode 返回的 authCode")
    nickname: str | None = None
    avatar_url: str | None = None
    table_token: str | None = None


class MemberRegisterRequest(BaseModel):
    nickname: str | None = None
    avatar_url: str | None = None
    phone: str | None = None
    enable_face: bool = False


@router.get("/tables")
async def list_dine_tables(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    store_id: int | None = None,
):
    """堂食选桌（须指定门店；生产不返回完整 qr_token）。"""
    from app.core.config import get_settings
    from app.core.exceptions import AppException

    if not store_id:
        raise AppException(40051, "缺少 store_id，请先扫码进入门店")

    settings = get_settings()
    q = (
        select(DiningTable)
        .where(DiningTable.status == 1, DiningTable.store_id == int(store_id))
        .order_by(DiningTable.table_no)
    )
    rows = (await db.execute(q)).scalars().all()
    expose_token = settings.is_development
    return success(
        [
            {
                "id": t.id,
                "table_no": t.table_no,
                "table_name": t.table_name,
                "store_id": t.store_id,
                **({"qr_token": t.qr_token} if expose_token else {}),
            }
            for t in rows
        ],
        request=request,
    )


@router.post("/tables/bind")
async def bind_dine_table(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """按 table_id 换取该桌 qr_token；须校验门店，防止跨店绑桌。"""
    from app.core.config import get_settings
    from app.core.exceptions import AppException

    body = await request.json()
    table_id = int(body.get("table_id") or 0)
    req_store = body.get("store_id")
    table = await db.get(DiningTable, table_id)
    if not table or table.status != 1:
        return success(None, message="桌台不存在或已禁用", request=request)

    settings = get_settings()
    # 生产必须带 store_id，且与桌台一致；开发可省略但传了仍校验
    if req_store is None and not settings.is_development:
        raise AppException(40050, "请先扫码进入门店后再选桌")
    if req_store is not None and int(req_store) != int(table.store_id):
        raise AppException(40052, "桌台不属于当前门店")

    return success(
        {
            "id": table.id,
            "table_no": table.table_no,
            "table_name": table.table_name,
            "store_id": table.store_id,
            "qr_token": table.qr_token,
        },
        request=request,
    )


@router.get("/entry")
async def scan_entry(
    request: Request,
    db: DbSession,
    token: str,
    channel: str = "wechat",
    store_id: int | None = None,
):
    from app.services.table_resolve import resolve_dining_table

    table = await resolve_dining_table(db, token, store_id=store_id)
    if not table:
        return success(None, message="无效桌台二维码", request=request)

    return success(
        {
            "table": {
                "id": table.id,
                "table_no": table.table_no,
                "table_name": table.table_name,
                "store_id": table.store_id,
                "qr_token": table.qr_token,
            },
            "channel": channel,
            "prompts": {
                "ask_register_member": True,
                "ask_face_welcome": True,
                "face_consent_text": "开启人脸迎宾后，到店可自动欢迎，并按您常点口味智能推荐。仅摄像头实时采集，可随时关闭。",
                "member_consent_text": "注册会员后系统会智能记录您的口味爱好，支持一键复购与到店迎宾推荐。",
            },
        },
        request=request,
    )


@router.post("/wechat/login")
async def wechat_mini_login(data: WechatMiniLoginRequest, request: Request, db: DbSession):
    auth = WechatMiniAuth()
    session = await auth.code2session(data.code)
    customer = await CustomerService.get_or_create_by_wechat(db, session["openid"], session.get("unionid"))
    if data.nickname:
        customer.nickname = data.nickname
        customer.display_name = customer.display_name or data.nickname
    if data.avatar_url:
        customer.avatar_url = data.avatar_url
    await db.flush()

    has_nickname = bool(customer.nickname or customer.display_name)
    customer_token = create_customer_token(customer.id, "wechat")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname or customer.display_name,
            "avatar_url": customer.avatar_url,
            # is_new = 库里刚创建且尚无昵称；禁止用 is_mock 冒充新客（否则开发环境每次都弹注册）
            "is_new": not has_nickname,
            "need_member_prompt": not has_nickname,
            "face_status": face_status,
            "table": table,
            "is_mock": bool(session.get("is_mock")),
        },
        request=request,
    )


@router.post("/alipay/login")
async def alipay_mini_login(data: AlipayMiniLoginRequest, request: Request, db: DbSession):
    auth = AlipayMiniAuth()
    session = await auth.auth_code2user(data.auth_code)
    customer = await CustomerService.get_or_create_by_alipay(db, session["alipay_user_id"])
    if data.nickname:
        customer.nickname = data.nickname
        customer.display_name = customer.display_name or data.nickname
    if data.avatar_url:
        customer.avatar_url = data.avatar_url
    await db.flush()

    has_nickname = bool(customer.nickname or customer.display_name)
    customer_token = create_customer_token(customer.id, "alipay")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname or customer.display_name,
            "avatar_url": customer.avatar_url,
            "is_new": not has_nickname,
            "need_member_prompt": not has_nickname,
            "face_status": face_status,
            "table": table,
            "is_mock": bool(session.get("is_mock")),
        },
        request=request,
    )


@router.post("/member/register")
async def register_member(
    data: MemberRegisterRequest,
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    if data.nickname:
        customer.nickname = data.nickname
        customer.display_name = data.nickname
    if data.avatar_url:
        customer.avatar_url = data.avatar_url
    if data.phone:
        customer = await CustomerService.try_link_by_phone(db, customer, data.phone)

    await db.flush()
    payload = {
        "customer_id": customer.id,
        "customer_no": customer.customer_no,
        "nickname": customer.nickname,
        "avatar_url": customer.avatar_url,
        "phone": customer.phone,
        "face_status": "NONE",
        "member_registered": True,
        "need_face_consent": bool(data.enable_face),
        # 若因手机号合并到老顾客，前端需刷新 token
        "customer_token": create_customer_token(customer.id, "merged"),
    }
    if data.enable_face:
        return success(payload, message="会员信息已保存，请完成摄像头人脸采集授权", request=request)
    return success(payload, request=request)


@router.post("/face/consent")
async def consent_face(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    image: UploadFile = File(...),
):
    """顾客主动授权：摄像头实拍后上传，服务端质量+活体校验并提取特征，禁止客户端伪造向量"""
    settings = get_settings()
    raw = await image.read()
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(raw) > max_size:
        raise AppException(40030, f"图片超过 {settings.max_upload_size_mb}MB")

    try:
        feature, quality = extract_face_embedding_with_quality(raw)
    except ValueError as e:
        raise AppException(40031, str(e)) from e

    profile = await FaceService.register_face(db, customer.id, feature)
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
            },
        },
        request=request,
    )


@router.post("/face/consent-base64")
async def consent_face_base64(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """小程序摄像头 takePhoto 后的 base64 授权（禁止相册选图）"""
    body = await request.json()
    b64 = body.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        raise AppException(40032, "缺少 image_base64")
    # 占位/过短串直接拒绝（如 H5 假数据）
    if len(b64) < 800:
        raise AppException(40033, "人脸图像无效，请使用小程序摄像头实时采集")
    try:
        raw = base64.b64decode(b64)
        if len(raw) < 1024:
            raise AppException(40034, "人脸图像过小，请重新正对摄像头采集")
        feature, quality = extract_face_embedding_with_quality(raw)
    except AppException:
        raise
    except ValueError as e:
        raise AppException(40031, str(e)) from e
    except Exception as e:
        raise AppException(40035, f"人脸采集失败: {e}") from e

    profile = await FaceService.register_face(db, customer.id, feature)
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
            },
        },
        request=request,
    )


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    await FaceService.revoke_face(db, customer.id)
    return success({"revoked": True}, request=request)


@router.get("/customers/me/face-status")
async def get_my_face_status(request: Request, db: DbSession, customer: CurrentCustomer):
    return success(await _face_status(db, customer.id), request=request)


async def _face_status(db, customer_id: int) -> dict:
    result = await db.execute(
        select(FaceProfile).where(
            FaceProfile.customer_id == customer_id,
            FaceProfile.consent_status == "AUTHORIZED",
        )
    )
    profile = result.scalar_one_or_none()
    return {
        "consent_status": profile.consent_status if profile else "NONE",
        "has_face": profile is not None,
    }


async def _resolve_table(db, token: str) -> dict | None:
    from app.services.table_resolve import resolve_dining_table

    table = await resolve_dining_table(db, token)
    if not table:
        return None
    return {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
        "store_id": table.store_id,
        "qr_token": table.qr_token,
    }
