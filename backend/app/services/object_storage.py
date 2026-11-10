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

    async def get_bytes(self, key: str) -> bytes:
        """读取对象内容；旧的自定义 storage adapter 可按需实现。"""
        raise NotImplementedError("当前对象存储适配器不支持读取对象")

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

    async def get_bytes(self, key: str) -> bytes:
        path = self._safe_resolve(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)

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
        access_key = (settings.s3_access_key or "").strip()
        secret_key = (settings.s3_secret_key or "").strip()
        endpoint = (settings.s3_endpoint or "").strip()
        if not (settings.s3_bucket or "").strip():
            raise RuntimeError("STORAGE_BACKEND=s3 但未配置 S3_BUCKET")
        if bool(access_key) != bool(secret_key):
            raise RuntimeError("S3_ACCESS_KEY 与 S3_SECRET_KEY 必须同时配置；使用 AWS IAM Role 时可同时留空")
        if endpoint:
            from urllib.parse import urlsplit

            parsed_endpoint = urlsplit(endpoint)
            if (
                parsed_endpoint.scheme not in ("http", "https")
                or not parsed_endpoint.hostname
                or parsed_endpoint.username
                or parsed_endpoint.password
                or parsed_endpoint.query
                or parsed_endpoint.fragment
            ):
                raise RuntimeError("S3_ENDPOINT 必须是无内嵌凭证/查询参数的有效 http:// 或 https:// URL")
        try:
            import boto3
            from botocore.client import Config
        except ImportError as e:
            raise RuntimeError("STORAGE_BACKEND=s3 需要安装 boto3") from e

        self.bucket = (settings.s3_bucket or "").strip()
        self.public_base = (settings.s3_public_base_url or "").strip().rstrip("/")
        if self.public_base:
            from urllib.parse import urlsplit

            public_url = urlsplit(self.public_base)
            if (
                public_url.scheme != "https"
                or not public_url.hostname
                or public_url.username
                or public_url.password
                or public_url.query
                or public_url.fragment
            ):
                raise RuntimeError(
                    "S3_PUBLIC_BASE_URL 必须是无凭证/查询参数的 HTTPS CDN 地址"
                )
        self.endpoint = endpoint.rstrip("/")
        client_options = {
            # endpoint_url 留空时由 Botocore 按 Region 解析 AWS S3 官方端点。
            "endpoint_url": endpoint or None,
            "region_name": settings.s3_region or "us-east-1",
            # 对象存储调用位于异步 API 中：通过 asyncio.to_thread 隔离同步 SDK，
            # 同时显式限制连接/读取超时、连接池和重试，避免网络异常拖垮工作进程。
            "config": Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                max_pool_connections=50,
                retries={"mode": "standard", "max_attempts": 3},
                tcp_keepalive=True,
            ),
        }
        if access_key:
            # 静态凭证必须成对提供；仅在显式配置时覆盖 SDK 凭证链。
            self.client = boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                **client_options,
            )
        else:
            # 无静态凭证时采用 AWS 标准凭证链（环境变量、共享配置、Web Identity、
            # ECS/EC2 IAM Role 等），并在启动阶段确认能解析到凭证。
            session = boto3.Session()
            if session.get_credentials() is None:
                raise RuntimeError(
                    "S3 未配置静态凭证，且 AWS 标准凭证链未找到可用凭证（请配置 IAM Role 或凭证提供器）"
                )
            self.client = session.client("s3", **client_options)

    def _put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def _delete_sync(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        await asyncio.to_thread(self._put_sync, key, data, content_type)
        # 只有商品图片与门店 Logo 属于公开媒体；其他前缀（头像等）不能
        # 通过公共 CDN/匿名路由暴露，等待独立的鉴权读取接口。
        if key.startswith(("products/", "stores/")):
            if self.public_base:
                return f"{self.public_base}/{key}"
            return f"/api/media/public/{key}"
        # 私有对象只保存不透明的内部标识，不把 S3/MinIO 管理端点暴露给客户端。
        return f"s3://{self.bucket}/{key}"

    async def get_bytes(self, key: str) -> bytes:
        def _read_object() -> bytes:
            try:
                result = self.client.get_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                response = getattr(exc, "response", {}) or {}
                code = str((response.get("Error") or {}).get("Code") or "")
                if code in ("NoSuchKey", "404", "NotFound"):
                    raise FileNotFoundError(key) from exc
                raise
            body = result.get("Body")
            if body is None:
                raise FileNotFoundError(key)
            try:
                return body.read()
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()

        return await asyncio.to_thread(_read_object)

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
    elif backend == "local":
        _storage = LocalObjectStorage()
        logger.info("Object storage: local dir=%s", settings.upload_dir)
    else:
        # 配置拼写错误不能静默切换本地磁盘，避免文件落在错误介质。
        raise RuntimeError(f"不支持的 STORAGE_BACKEND：{backend!r}（仅支持 local / s3）")
    return _storage


def reset_storage_for_tests() -> None:
    """测试用：清空单例。"""
    global _storage
    _storage = None


def make_object_key(subdir: str, ext: str) -> str:
    return f"{subdir.strip('/')}/{uuid.uuid4().hex}.{ext.lstrip('.')}"
