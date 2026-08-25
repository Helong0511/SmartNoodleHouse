"""小程序登录与扫码点餐入口"""
import base64

from pydantic import BaseModel, Field
from sqlalchemy import select

from fastapi import APIRouter, File, Request, UploadFile

from app.api.deps import CurrentCustomer, DbSession
from app.core.security import create_customer_token
from app.core.config import get_settings
from app.core.response import success
from app.models import Customer, DiningTable, FaceProfile
from app.services.customer_service import CustomerService
from app.services.face_extractor import extract_face_embedding
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


@router.get("/entry")
async def scan_entry(request: Request, db: DbSession, token: str, channel: str = "wechat"):
    result = await db.execute(
        select(DiningTable).where(DiningTable.qr_token == token, DiningTable.status == 1)
    )
    table = result.scalar_one_or_none()
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
                "face_consent_text": "开启人脸迎宾后，到店可自动欢迎并推荐您常点口味。仅在您主动授权后生效，可随时关闭。",
                "member_consent_text": "注册会员后可查看历史订单、口味偏好，并支持一键复购。",
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

    customer_token = create_customer_token(customer.id, "wechat")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname,
            "avatar_url": customer.avatar_url,
            "is_new": session.get("is_mock", False) or not customer.nickname,
            "need_member_prompt": not bool(customer.nickname),
            "face_status": face_status,
            "table": table,
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

    customer_token = create_customer_token(customer.id, "alipay")
    face_status = await _face_status(db, customer.id)
    table = await _resolve_table(db, data.table_token) if data.table_token else None

    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "customer_token": customer_token,
            "nickname": customer.nickname,
            "avatar_url": customer.avatar_url,
            "is_new": session.get("is_mock", False) or not customer.nickname,
            "need_member_prompt": not bool(customer.nickname),
            "face_status": face_status,
            "table": table,
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
        customer.phone = data.phone

    face_status = "NONE"
    if data.enable_face:
        return success(None, message="请通过 /miniprogram/face/consent 上传人脸照片完成授权", request=request)

    await db.flush()
    return success(
        {
            "customer_id": customer.id,
            "customer_no": customer.customer_no,
            "nickname": customer.nickname,
            "face_status": face_status,
            "member_registered": True,
        },
        request=request,
    )


@router.post("/face/consent")
async def consent_face(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
    image: UploadFile = File(...),
):
    """顾客主动授权：服务端提取人脸特征，禁止客户端伪造向量"""
    settings = get_settings()
    raw = await image.read()
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(raw) > max_size:
        return success(None, message=f"图片超过 {settings.max_upload_size_mb}MB", request=request)

    try:
        feature = extract_face_embedding(raw)
    except ValueError as e:
        return success(None, message=str(e), request=request)

    profile = await FaceService.register_face(db, customer.id, feature)
    return success({"profile_id": profile.id, "consent_status": profile.consent_status}, request=request)


@router.post("/face/consent-base64")
async def consent_face_base64(
    request: Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """小程序 base64 图片授权（wx.chooseImage + readFileBase64）"""
    body = await request.json()
    b64 = body.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        return success(None, message="缺少 image_base64", request=request)
    try:
        raw = base64.b64decode(b64)
        feature = extract_face_embedding(raw)
    except Exception as e:
        return success(None, message=f"人脸采集失败: {e}", request=request)

    profile = await FaceService.register_face(db, customer.id, feature)
    return success({"profile_id": profile.id, "consent_status": profile.consent_status}, request=request)


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
    result = await db.execute(
        select(DiningTable).where(DiningTable.qr_token == token, DiningTable.status == 1)
    )
    table = result.scalar_one_or_none()
    if not table:
        return None
    return {
        "id": table.id,
        "table_no": table.table_no,
        "table_name": table.table_name,
        "store_id": table.store_id,
        "qr_token": table.qr_token,
    }
