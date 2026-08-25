import mimetypes
import secrets
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import AppException

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


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

    upload_dir = Path(settings.upload_dir) / subdir
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = upload_dir / filename
    # 防路径穿越
    if not str(filepath.resolve()).startswith(str(upload_dir.resolve())):
        raise AppException(40023, "非法文件路径")

    filepath.write_bytes(data)
    return f"/uploads/{subdir}/{filename}"
