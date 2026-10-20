"""小程序人脸授权：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

import asyncio
import base64

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentCustomer, DbSession
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.response import success
from app.models import FaceProfile
from app.services.face_extractor import extract_face_embedding_with_quality
from app.services.face_service import FaceService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

class FaceConsentBase64Request(BaseModel):
    image_base64: str = Field(default="", description="摄像头实拍 JPEG/PNG 的 base64")
    liveness_token: str | None = None
    token: str | None = Field(default=None, description="liveness_token 别名")
    action_completed: bool = True
    # 显式声明以便拒绝客户端上传特征（禁止绕过服务端提取）
    feature: object | None = None
    embedding: object | None = None
    feature_vector: object | None = None


async def _require_face_welcome_enabled(db: AsyncSession) -> None:
    """功能总开关关闭时拒绝注册/挑战/撤销以外的开启路径。"""
    from app.services.system_settings_service import SystemSettingsService

    settings = get_settings()
    enabled = await SystemSettingsService.effective_bool(
        db, "enable_face_welcome", settings.enable_face_welcome, store_id=None
    )
    if not enabled:
        raise AppException(40340, "人脸迎宾功能未开启")


@router.get("/face/liveness/challenge")
async def face_liveness_challenge(request: Request, db: DbSession, customer: CurrentCustomer):
    """下发一次性活体挑战（动作 + token）。mode=none 时返回空 token。"""
    await _require_face_welcome_enabled(db)
    from app.services.face_liveness_service import generate_liveness_challenge

    challenge = await generate_liveness_challenge(customer_id=customer.id)
    return success(challenge, request=request)


@router.post("/face/consent-base64")
async def consent_face_base64(
    request: Request,
    body: FaceConsentBase64Request,
    db: DbSession,
    customer: CurrentCustomer,
):
    """小程序摄像头 takePhoto 后的 base64 授权（禁止相册选图 / 禁止客户端上传 embedding）"""
    await _require_face_welcome_enabled(db)
    if body.feature is not None or body.embedding is not None or body.feature_vector is not None:
        raise AppException(40036, "禁止客户端上传人脸特征向量，仅接受摄像头实拍图片")
    liveness_mode = await _enforce_liveness_challenge(
        customer.id,
        liveness_token=body.liveness_token or body.token,
        action_completed=bool(body.action_completed),
    )
    b64 = body.image_base64 or ""
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    if not b64:
        raise AppException(40032, "缺少 image_base64")
    # 占位/过短串直接拒绝（如 H5 假数据）
    if len(b64) < 800:
        raise AppException(40033, "人脸图像无效，请使用小程序摄像头实时采集")
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    mb = await SystemSettingsService.get_int(db, "max_upload_size_mb", settings.max_upload_size_mb)
    max_b64 = mb * 1024 * 1024 * 2  # base64 膨胀约 4/3，再留余量
    if len(b64) > max_b64:
        raise AppException(40030, f"图片超过 {mb}MB")
    try:
        raw = base64.b64decode(b64, validate=False)
        max_size = mb * 1024 * 1024
        if len(raw) > max_size:
            raise AppException(40030, f"图片超过 {mb}MB")
        if len(raw) < 1024:
            raise AppException(40034, "人脸图像过小，请重新正对摄像头采集")
        feature, quality = await asyncio.to_thread(extract_face_embedding_with_quality, raw)
    except AppException:
        raise
    except ValueError as e:
        raise AppException(40031, str(e)) from e
    except Exception as e:
        raise AppException(40035, f"人脸采集失败: {e}") from e

    profile = await FaceService.register_face(db, customer.id, feature)
    from app.services.audit_service import AuditService

    await AuditService.log_face_consent(
        db,
        customer_id=customer.id,
        profile_id=profile.id,
        request=request,
        extra={
            "via": "base64",
            "anti_spoof_level": "basic",
            "liveness_mode": liveness_mode,
        },
    )
    return success(
        {
            "profile_id": profile.id,
            "consent_status": profile.consent_status,
            "anti_spoof_level": "basic",
            "liveness_mode": liveness_mode,
            "quality": {
                "sharpness": quality.get("sharpness"),
                "brightness": quality.get("brightness"),
                "face_ratio": quality.get("face_ratio"),
                "liveness": quality.get("liveness"),
                "anti_spoof_level": quality.get("anti_spoof_level", "basic"),
            },
        },
        request=request,
    )


@router.post("/face/revoke")
async def revoke_face(request: Request, db: DbSession, customer: CurrentCustomer):
    n = await FaceService.revoke_face(db, customer.id, request=request)
    return success({"revoked": True, "count": n}, request=request)


async def _enforce_liveness_challenge(
    customer_id: int,
    *,
    liveness_token: str | None,
    action_completed: bool = True,
) -> str:
    """challenge 模式下强制一次性活体 token；none 模式直接放行。返回实际模式。"""
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if mode == "none":
        return "none"
    from app.services.face_liveness_service import verify_liveness_challenge

    ok = await verify_liveness_challenge(
        (liveness_token or "").strip(),
        action_completed=bool(action_completed),
        customer_id=int(customer_id),
    )
    if not ok:
        raise AppException(40037, "活体校验未通过或已过期，请重新完成动作挑战后拍照")
    return "challenge"


async def _face_status(db, customer_id: int) -> dict:
    result = await db.execute(
        select(FaceProfile).where(
            FaceProfile.customer_id == customer_id,
            FaceProfile.consent_status == "AUTHORIZED",
        )
    )
    profile = result.scalar_one_or_none()
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    return {
        "consent_status": profile.consent_status if profile else "NONE",
        "has_face": profile is not None,
        "liveness_mode": mode,
    }
