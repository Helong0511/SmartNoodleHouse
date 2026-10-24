"""对象存储抽象：local / s3（含 MinIO 兼容）"""
from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ObjectStorage(ABC):
    @abstractmethod
    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """上传并返回可访问 URL（相对或绝对）。"""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """删除对象；返回是否确实删掉了文件/对象。"""


class LocalObjectStorage(ObjectStorage):
    @staticmethod
    def _safe_resolve(key: str) -> Path:
        """解析并校验 key 不逃逸出上传根目录（commonpath 防兄弟目录前缀绕过）。

        相对 UPLOAD_DIR 按进程 cwd 解析（Windows 本地调试与 Ubuntu systemd WorkingDirectory 均适用）。
        """
        settings = get_settings()
        root = Path(settings.upload_dir).expanduser().resolve()
        # key 统一正斜杠，Path 在 Win/Linux 均可拼接
        safe_key = str(key or "").replace("\\", "/").lstrip("/")
        resolved = (root / safe_key).resolve()
        import os

        if os.path.commonpath([str(root), str(resolved)]) != str(root):
            raise ValueError("非法存储路径")
        return resolved

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._safe_resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return f"/uploads/{key.replace(chr(92), '/')}"

    async def delete(self, key: str) -> bool:
        path = self._safe_resolve(key)
        if path.is_file():
            await asyncio.to_thread(path.unlink)
            return True
        return False


class S3ObjectStorage(ObjectStorage):
    """boto3 同步客户端；put/delete 经 asyncio.to_thread，避免阻塞事件循环。"""

    def __init__(self):
        settings = get_settings()
        if not (
            (settings.s3_endpoint or "").strip()
            and (settings.s3_access_key or "").strip()
            and (settings.s3_secret_key or "").strip()
            and (settings.s3_bucket or "").strip()
        ):
            raise RuntimeError("STORAGE_BACKEND=s3 但 S3 配置不完整")
        try:
            import boto3
            from botocore.client import Config
        except ImportError as e:
            raise RuntimeError("STORAGE_BACKEND=s3 需要安装 boto3") from e

        self.bucket = settings.s3_bucket
        self.public_base = (settings.s3_public_base_url or "").rstrip("/")
        self.endpoint = (settings.s3_endpoint or "").rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region or "us-east-1",
            config=Config(signature_version="s3v4"),
        )

    def _put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def _delete_sync(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        await asyncio.to_thread(self._put_sync, key, data, content_type)
        if self.public_base:
            return f"{self.public_base}/{key}"
        if self.endpoint:
            return f"{self.endpoint}/{self.bucket}/{key}"
        return f"s3://{self.bucket}/{key}"

    async def delete(self, key: str) -> bool:
        await asyncio.to_thread(self._delete_sync, key)
        return True


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """初始化对象存储。

    STORAGE_BACKEND=s3 时初始化失败**不再**静默降级本地（生产易误以为已上云）。
    """
    global _storage
    if _storage is not None:
        return _storage
    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "s3":
        _storage = S3ObjectStorage()
        logger.info("Object storage: S3/MinIO bucket=%s", settings.s3_bucket)
    else:
        _storage = LocalObjectStorage()
        logger.info("Object storage: local dir=%s", settings.upload_dir)
    return _storage


def reset_storage_for_tests() -> None:
    """测试用：清空单例。"""
    global _storage
    _storage = None


def make_object_key(subdir: str, ext: str) -> str:
    return f"{subdir.strip('/')}/{uuid.uuid4().hex}.{ext.lstrip('.')}"
