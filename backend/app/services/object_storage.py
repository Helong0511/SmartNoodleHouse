"""对象存储抽象：local / s3（含 MinIO 兼容）"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urljoin

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ObjectStorage(ABC):
    @abstractmethod
    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """上传并返回可访问 URL（相对或绝对）。"""

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...


class LocalObjectStorage(ObjectStorage):
    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        settings = get_settings()
        path = Path(settings.upload_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve()
        root = Path(settings.upload_dir).resolve()
        if not str(resolved).startswith(str(root)):
            raise ValueError("非法存储路径")
        path.write_bytes(data)
        return f"/uploads/{key.replace(chr(92), '/')}"

    async def delete(self, key: str) -> None:
        settings = get_settings()
        path = Path(settings.upload_dir) / key
        if path.is_file():
            path.unlink()


class S3ObjectStorage(ObjectStorage):
    """需要 boto3；未安装时启动会降级提示。"""

    def __init__(self):
        import boto3
        from botocore.client import Config

        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.public_base = (settings.s3_public_base_url or "").rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region or "us-east-1",
            config=Config(signature_version="s3v4"),
        )

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        # boto3 同步 API，放到线程池亦可；体量小直接调
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        if self.public_base:
            return f"{self.public_base}/{key}"
        settings = get_settings()
        if settings.s3_endpoint:
            return f"{settings.s3_endpoint.rstrip('/')}/{self.bucket}/{key}"
        return f"s3://{self.bucket}/{key}"

    async def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is not None:
        return _storage
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "s3":
        try:
            _storage = S3ObjectStorage()
            logger.info("Object storage: S3/MinIO bucket=%s", settings.s3_bucket)
        except Exception as e:
            logger.error("S3 初始化失败，降级本地存储: %s", e)
            _storage = LocalObjectStorage()
    else:
        _storage = LocalObjectStorage()
    return _storage


def make_object_key(subdir: str, ext: str) -> str:
    return f"{subdir.strip('/')}/{uuid.uuid4().hex}.{ext.lstrip('.')}"
