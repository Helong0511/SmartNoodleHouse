import ipaddress
import logging
import mimetypes
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.services.object_storage import get_storage, make_object_key

logger = logging.getLogger(__name__)

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}
_UNSAFE_URL_MSG = "不允许拉取该头像地址"

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


def sniff_image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif", "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    raise AppException(40021, "远程文件不是支持的图片格式")


def _assert_ip_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise AppException(40021, _UNSAFE_URL_MSG)


def _assert_host_public(host: str) -> None:
    try:
        _assert_ip_public(ipaddress.ip_address(host))
        return
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise AppException(40021, "无法解析头像地址") from exc
    if not infos:
        raise AppException(40021, "无法解析头像地址")
    checked = False
    for info in infos:
        addr = info[4][0]
        try:
            _assert_ip_public(ipaddress.ip_address(addr))
            checked = True
        except ValueError:
            continue
    if not checked:
        raise AppException(40021, "无法解析头像地址")


def assert_public_http_url(url: str) -> None:
    """拒绝非 http(s)、内网/回环地址，降低 SSRF 风险。"""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise AppException(40021, "头像地址必须是 http/https")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS:
        raise AppException(40021, _UNSAFE_URL_MSG)
    if host.endswith(".local") or host.endswith(".internal"):
        raise AppException(40021, _UNSAFE_URL_MSG)
    _assert_host_public(host)


async def save_image_bytes(
    data: bytes,
    *,
    ext: str,
    content_type: str | None,
    subdir: str,
) -> str:
    settings = get_settings()
    if ext not in settings.allowed_extensions:
        raise AppException(40020, f"不允许的文件扩展名: {ext}")
    if content_type not in ALLOWED_MIME:
        raise AppException(40021, f"不允许的MIME类型: {content_type}")
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if len(data) > max_size:
        raise AppException(40022, f"文件大小超过限制 {settings.max_upload_size_mb}MB")
    if not data:
        raise AppException(40025, "空文件")
    _validate_magic(ext, data)
    key = make_object_key(subdir, ext)
    return await get_storage().put_bytes(key, data, content_type=content_type or "application/octet-stream")


async def save_upload(file: UploadFile, subdir: str = "products") -> str:
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    data = await file.read()
    return await save_image_bytes(data, ext=ext, content_type=content_type, subdir=subdir)


async def fetch_and_save_remote_image(url: str, subdir: str = "avatars") -> str:
    """下载公网头像（支付宝/微信 CDN）并保存为 /uploads 路径。"""
    import httpx

    current = str(url or "").strip()
    settings = get_settings()
    max_size = settings.max_upload_size_mb * 1024 * 1024
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        resp = None
        for _ in range(4):
            assert_public_http_url(current)
            resp = await client.get(current, headers={"User-Agent": "SmartNoodleHouse/1.0"})
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise AppException(40021, "无法下载头像")
                current = str(resp.url.join(loc))
                continue
            break
        else:
            raise AppException(40021, "头像地址跳转过多")
        if resp is None or resp.status_code >= 400:
            code = getattr(resp, "status_code", "?")
            raise AppException(40021, f"无法下载头像({code})")
        data = resp.content
    if len(data) > max_size:
        raise AppException(40022, f"文件大小超过限制 {settings.max_upload_size_mb}MB")
    ext, content_type = sniff_image_type(data)
    return await save_image_bytes(data, ext=ext, content_type=content_type, subdir=subdir)
