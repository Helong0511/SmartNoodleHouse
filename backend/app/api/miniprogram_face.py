"""小程序人脸授权：从 miniprogram 路由拆出，路径仍挂在 /api/miniprogram。"""
from __future__ import annotations

import asyncio
import base64
import binascii

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
from app.services.face_feature_gate import FACE_RECOGNITION_TEMPORARILY_DISABLED
from app.services.face_service import FaceService

router = APIRouter(prefix="/miniprogram", tags=["小程序"])

# Bump when the purpose, processing method, or disclosure changes.
FACE_CONSENT_VERSION = "face-welcome-2026-11-v1"

def _is_supported_face_image(raw: bytes) -> bool:
    """只接受真实 JPEG/PNG 文件头；不能用文本转义序列替代二进制魔数。"""
    return raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")



class FaceConsentBase64Request(BaseModel):
    image_base64: str = Field(default="", description="摄像头实拍 JPEG/PNG 的 base64")
    face_consent_accepted: bool = Field(default=False, description="顾客对人脸敏感个人信息处理的单独同意")
    face_consent_version: str | None = Field(default=None, max_length=40)
    liveness_token: str | None = None
    token: str | None = Field(default=None, description="liveness_token 别名")
    action_completed: bool = False
    claimed_action: str | None = Field(default=None, max_length=32)
    # 显式声明以便拒绝客户端上传特征（禁止绕过服务端提取）
    feature: object | None = None
    embedding: object | None = None
    feature_vector: object | None = None


async def _require_face_welcome_enabled(db: AsyncSession) -> None:
    """功能总开关关闭时拒绝注册/挑战/撤销以外的开启路径。"""
    if FACE_RECOGNITION_TEMPORARILY_DISABLED:
        raise AppException(40344, "人脸识别功能临时关闭，待完成模型商业授权核验后再恢复")
    from app.services.system_settings_service import SystemSettingsService

    settings = get_settings()
    if settings.is_production and not settings.face_recognition_legal_reviewed:
        raise AppException(40341, "生产环境人脸识别尚未完成合规审核")
    if settings.is_production and not settings.face_model_commercial_license_confirmed:
        raise AppException(40343, "生产环境人脸模型尚未确认商业授权")
    enabled = await SystemSettingsService.effective_bool(
        db, "enable_face_welcome", settings.enable_face_welcome, store_id=None
    )
    if not enabled:
        raise AppException(40340, "人脸迎宾功能未开启")


@router.get("/face/liveness/challenge")
async def face_liveness_challenge(request: Request, db: DbSession, customer: CurrentCustomer):
    """下发一次性活体挑战（动作 + token）。mode=none 时返回空 token。"""
    await _require_face_welcome_enabled(db)
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if settings.is_production and mode == "none":
        raise AppException(40342, "生产环境不允许关闭人脸动作挑战")
    from app.services.face_liveness_service import generate_liveness_challenge

    challenge = await generate_liveness_challenge(customer_id=customer.id)
    challenge["anti_spoof_level"] = "basic"
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
    # General privacy-policy agreement and liveness action do not replace separate face-data consent.
    if body.face_consent_accepted is not True or body.face_consent_version != FACE_CONSENT_VERSION:
        raise AppException(40344, "请先阅读人脸信息处理说明，并单独确认本次人脸信息处理")
    if body.feature is not None or body.embedding is not None or body.feature_vector is not None:
        raise AppException(40036, "禁止客户端上传人脸特征向量，仅接受摄像头实拍图片")
    liveness_mode = await _enforce_liveness_challenge(
        customer.id,
        liveness_token=body.liveness_token or body.token,
        action_completed=bool(body.action_completed),
        claimed_action=body.claimed_action,
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
        raw = base64.b64decode(b64, validate=True)
        max_size = mb * 1024 * 1024
        if len(raw) > max_size:
            raise AppException(40030, f"图片超过 {mb}MB")
        if len(raw) < 1024:
            raise AppException(40034, "人脸图像过小，请重新正对摄像头采集")
        # 该接口只接收小程序摄像头 JPEG/PNG，拒绝 TIFF/BMP/GIF 等额外解码面。
        if not _is_supported_face_image(raw):
            raise AppException(40031, "仅支持小程序摄像头采集的 JPEG/PNG 图片")
        feature, quality = await asyncio.to_thread(extract_face_embedding_with_quality, raw)
    except AppException:
        raise
    except (ValueError, binascii.Error) as e:
        raise AppException(40031, "人脸图片 Base64 格式无效") from e
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
            "face_consent_accepted": True,
            "face_consent_version": FACE_CONSENT_VERSION,
            "face_consent_purpose": "in_store_welcome",
            "face_consent_remote_storage_disclosed": True,
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
    claimed_action: str | None = None,
) -> str:
    """challenge 模式下强制一次性活体 token；none 模式直接放行。返回实际模式。"""
    settings = get_settings()
    mode = (getattr(settings, "face_liveness_mode", "challenge") or "challenge").strip().lower()
    if mode == "none":
        # none 仅可用于本地开发；生产环境禁止通过配置关闭挑战校验。
        if settings.is_production:
            raise AppException(40342, "生产环境不允许关闭人脸动作挑战")
        return "none"
    from app.services.face_liveness_service import verify_liveness_challenge

    if not claimed_action:
        raise AppException(40037, "缺少本次随机动作标识，请重新获取挑战")
    ok = await verify_liveness_challenge(
        (liveness_token or "").strip(),
        action_completed=bool(action_completed),
        claimed_action=str(claimed_action),
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
