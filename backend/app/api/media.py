"""公共媒体读取：仅允许数据库正在引用的商品图片与门店 Logo。"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models import (
    Customer,
    CustomerAlipayAccount,
    CustomerWechatAccount,
    Product,
    ProductImage,
    Store,
)
from app.services.object_storage import get_storage
from app.services.private_media import verify_private_avatar_signature

logger = logging.getLogger(__name__)
router = APIRouter(tags=["公共媒体"])

# 只暴露应用上传流程生成的公开商品图片与门店 Logo；客户头像/人脸等目录不在白名单。
_KEY_RE = re.compile(r"(?:products|stores)/[0-9a-f]{32}\.(?:webp|jpg|jpeg|png|gif)")
_CONTENT_TYPES = {
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
}




_PRIVATE_AVATAR_RE = re.compile(r"[0-9a-f]{32}\.(?:webp|jpg|jpeg|png|gif)")


@router.get("/media/private/avatars/{filename}", include_in_schema=False)
async def get_private_avatar(
    filename: str,
    customer_id: int,
    expires: int,
    signature: str,
    db: DbSession,
) -> Response:
    """短时签名头像读取；对象必须仍由该顾客主档或渠道资料引用。"""
    if not _PRIVATE_AVATAR_RE.fullmatch(filename):
        raise AppException(40425, "媒体不存在", status_code=404)
    key = f"avatars/{filename}"
    if not verify_private_avatar_signature(
        customer_id=customer_id,
        key=key,
        expires=expires,
        signature=signature,
    ):
        raise AppException(40425, "媒体不存在或链接已过期", status_code=404)

    private_path = f"/api/media/private/{key}"
    bucket = str(get_settings().s3_bucket or "").strip()
    legacy_s3_path = f"s3://{bucket}/{key}" if bucket else None
    allowed_refs = [private_path, f"/uploads/{key}"]
    if legacy_s3_path:
        allowed_refs.append(legacy_s3_path)
    referenced = await db.scalar(
        select(Customer.id)
        .where(
            Customer.id == int(customer_id),
            Customer.avatar_url.in_(allowed_refs),
        )
        .limit(1)
    )
    if referenced is None:
        referenced = await db.scalar(
            select(CustomerWechatAccount.customer_id)
            .where(
                CustomerWechatAccount.customer_id == int(customer_id),
                CustomerWechatAccount.avatar_url.in_(allowed_refs),
            )
            .limit(1)
        )
    if referenced is None:
        referenced = await db.scalar(
            select(CustomerAlipayAccount.customer_id)
            .where(
                CustomerAlipayAccount.customer_id == int(customer_id),
                CustomerAlipayAccount.avatar_url.in_(allowed_refs),
            )
            .limit(1)
        )
    if referenced is None:
        raise AppException(40425, "媒体不存在", status_code=404)

    try:
        body = await get_storage().get_bytes(key)
    except FileNotFoundError:
        raise AppException(40425, "媒体不存在", status_code=404) from None
    except Exception as exc:
        logger.exception("读取私有头像失败: customer_id=%s key=%s", customer_id, key)
        raise AppException(50301, "媒体暂时不可用", status_code=503) from exc

    ext = filename.rsplit(".", 1)[-1].lower()
    return Response(
        content=body,
        media_type=_CONTENT_TYPES[ext],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.get("/media/public/{key:path}", include_in_schema=False)
async def get_public_media(key: str, db: DbSession) -> Response:
    """只读取仍被商品/门店记录引用的公开图片，避免将 S3 桶变成任意对象代理。"""
    if not _KEY_RE.fullmatch(key):
        raise AppException(40425, "媒体不存在", status_code=404)

    public_path = f"/api/media/public/{key}"
    referenced = await db.scalar(
        select(Product.id).where(Product.main_image == public_path).limit(1)
    )
    if referenced is None:
        referenced = await db.scalar(
            select(ProductImage.id).where(ProductImage.image_url == public_path).limit(1)
        )
    if referenced is None:
        referenced = await db.scalar(
            select(Store.id).where(Store.logo == public_path).limit(1)
        )
    if referenced is None:
        raise AppException(40425, "媒体不存在", status_code=404)

    try:
        body = await get_storage().get_bytes(key)
    except FileNotFoundError:
        raise AppException(40425, "媒体不存在", status_code=404) from None
    except Exception as exc:
        logger.exception("读取公共媒体失败: key=%s", key)
        raise AppException(50301, "媒体暂时不可用", status_code=503) from exc

    ext = key.rsplit(".", 1)[-1].lower()
    return Response(
        content=body,
        media_type=_CONTENT_TYPES[ext],
        headers={
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
