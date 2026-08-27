import logging
import mimetypes
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.services.object_storage import get_storage, make_object_key

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# 魔数校验，防扩展名伪造
_MAGIC = {
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),  # 还需 WEBP 在 offset 8
}


def _validate_magic(ext: str, data: bytes) -> None:
    ext = ext.lower()
    if ext not in _MAGIC:
        return
    headers = _MAGIC[ext]
    if not any(data.startswith(h) for h in headers):
        raise AppException(40024, "文件内容与扩展名不符")
    if ext == "webp" and (len(data) < 12 or data[8:12] != b"WEBP"):
        raise AppException(40024, "无效的 WebP 文件")


async def save_upload(file: UploadFile, subdir: str = "products") -> str:
    settings = get_settings()
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in settings.allowed_extensions:
        raise AppException(40020, f"不允许的文件扩展名: {ext}")

    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    if content_type not in ALLOWED_MIME:
        raise AppException(40021, f"不允许的MIME类型: {content_type}")

    data = await file.read()
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > max_size:
        raise AppException(40022, f"文件大小超过限制 {settings.max_upload_size_mb}MB")
    if not data:
        raise AppException(40025, "空文件")
    _validate_magic(ext, data)

    key = make_object_key(subdir, ext)
    url = await get_storage().put_bytes(key, data, content_type=content_type or "application/octet-stream")
    return url
