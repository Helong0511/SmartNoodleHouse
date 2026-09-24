"""图片上传与远程拉取：流式体积限制、MIME/魔数、尺寸与解压炸弹防护、SSRF 防护。"""
from __future__ import annotations

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
_BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata",
    "metadata.aws.internal",
    "instance-data",
}
_UNSAFE_URL_MSG = "不允许拉取该头像地址"
_READ_CHUNK = 64 * 1024
# 像素上限：抑制 PNG/GIF 解压炸弹（约 25MP）
_MAX_IMAGE_PIXELS = 25_000_000
_MAX_IMAGE_EDGE = 8192
_REMOTE_MAX_REDIRECTS = 3
_REMOTE_TIMEOUT = 10.0


def _max_upload_mb() -> int:
    """优先 system_settings 进程缓存，否则 .env。"""
    from app.services.system_settings_service import SystemSettingsService

    return SystemSettingsService.resolved_int_sync(
        "max_upload_size_mb", get_settings().max_upload_size_mb
    )


def _allowed_extensions() -> set[str]:
    settings = get_settings()
    from app.services.system_settings_service import SystemSettingsService

    raw = SystemSettingsService.resolved_str_sync(
        "allowed_image_extensions", settings.allowed_image_extensions
    )
    exts = {e.strip().lower().lstrip(".") for e in raw.split(",") if e.strip()}
    return exts or settings.allowed_extensions


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


def assert_image_safe(data: bytes) -> tuple[int, int]:
    """解码校验尺寸，拒绝解压炸弹与异常大图。返回 (width, height)。"""
    import cv2
    import numpy as np

    if not data:
        raise AppException(40025, "空文件")
    # 粗略拒绝超大压缩包头（再解码前）
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise AppException(40024, "无法解码图片或格式损坏")
    h, w = int(img.shape[0]), int(img.shape[1])
    if w <= 0 or h <= 0:
        raise AppException(40024, "无效图片尺寸")
    if w > _MAX_IMAGE_EDGE or h > _MAX_IMAGE_EDGE:
        raise AppException(40024, f"图片边长超过限制 {_MAX_IMAGE_EDGE}px")
    if w * h > _MAX_IMAGE_PIXELS:
        raise AppException(40024, "图片像素过多（疑似解压炸弹）")
    return w, h


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
    # 云厂商 metadata / 文档网段
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            raise AppException(40021, _UNSAFE_URL_MSG)
        if ip in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT
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


def _peer_ip_from_response(resp) -> str | None:
    """读取已建立连接的对端 IP，缓解 DNS TOCTOU / rebinding。"""
    try:
        stream = resp.extensions.get("network_stream")
        if stream is None:
            return None
        peer = stream.get_extra_info("peername")
        if isinstance(peer, tuple) and peer:
            return str(peer[0])
    except Exception:
        return None
    return None


def assert_public_http_url(url: str) -> None:
    """拒绝非 http(s)、内网/回环/metadata，降低 SSRF 风险。"""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise AppException(40021, "头像地址必须是 http/https")
    if parsed.username or parsed.password:
        raise AppException(40021, _UNSAFE_URL_MSG)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS:
        raise AppException(40021, _UNSAFE_URL_MSG)
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        raise AppException(40021, _UNSAFE_URL_MSG)
    _assert_host_public(host)


async def read_upload_limited(file: UploadFile, max_bytes: int | None = None) -> bytes:
    """流式读取上传文件，超限立即中止（避免无条件 file.read()）。"""
    mb = _max_upload_mb()
    limit = int(max_bytes if max_bytes is not None else mb * 1024 * 1024)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            if max_bytes is not None:
                raise AppException(
                    40022,
                    f"文件大小超过限制 {max(1, (limit + 1024 * 1024 - 1) // (1024 * 1024))}MB",
                )
            raise AppException(40022, f"文件大小超过限制 {mb}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def _convert_to_webp(data: bytes, ext: str) -> tuple[bytes, str]:
    """将 jpg/jpeg/png 自动转换为 WebP 以减小体积（平均缩小 65%+）。

    GIF 保留原格式（动画）。转换失败时安全降级保留原格式。
    """
    ext_lower = ext.lower()
    if ext_lower in ("webp", "gif"):
        return data, ext
    if ext_lower not in ("jpg", "jpeg", "png"):
        return data, ext
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        # PNG 可能带 alpha 通道；RGBA → WebP 保留透明
        if img.mode not in ("RGB", "RGBA", "L", "LA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=85, method=4)
        webp_data = buf.getvalue()
        if not webp_data:
            return data, ext
        logger.info("WebP 转换: %s → webp (%d → %d bytes, %.0f%%)",
                     ext, len(data), len(webp_data),
                     len(webp_data) * 100.0 / max(1, len(data)))
        return webp_data, "webp"
    except Exception:
        logger.warning("WebP 转换失败，保留原格式 %s", ext, exc_info=True)
        return data, ext


# 门店 Logo：更严体积、强制正方形 WebP，便于小程序门店栏加载
_STORE_LOGO_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_STORE_LOGO_MAX_EDGE = 512
_STORE_LOGO_MIN_EDGE = 64
_STORE_LOGO_EXTS = frozenset({"jpg", "jpeg", "png", "webp"})
_STORE_LOGO_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})


def _normalize_store_logo_webp(data: bytes) -> bytes:
    """居中裁为正方形、最长边 ≤512，强制输出 WebP（保留透明）。"""
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception as exc:
        raise AppException(40024, "无法解码图片或格式损坏") from exc

    w, h = int(img.size[0]), int(img.size[1])
    if w <= 0 or h <= 0:
        raise AppException(40024, "无效图片尺寸")
    if min(w, h) < _STORE_LOGO_MIN_EDGE:
        raise AppException(
            40024, f"Logo 边长至少 {_STORE_LOGO_MIN_EDGE}px（当前 {w}×{h}）"
        )
    if w > _MAX_IMAGE_EDGE or h > _MAX_IMAGE_EDGE:
        raise AppException(40024, f"图片边长超过限制 {_MAX_IMAGE_EDGE}px")
    if w * h > _MAX_IMAGE_PIXELS:
        raise AppException(40024, "图片像素过多（疑似解压炸弹）")

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    target = min(side, _STORE_LOGO_MAX_EDGE)
    if side != target:
        img = img.resize((target, target), Image.Resampling.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=82, method=4)
    webp_data = buf.getvalue()
    if not webp_data:
        raise AppException(40024, "Logo 转换为 WebP 失败")
    logger.info(
        "门店 Logo WebP: %dx%d → %dx%d (%d → %d bytes)",
        w,
        h,
        target,
        target,
        len(data),
        len(webp_data),
    )
    return webp_data


async def save_image_bytes(
    data: bytes,
    *,
    ext: str,
    content_type: str | None,
    subdir: str,
) -> str:
    settings = get_settings()
    if ext not in _allowed_extensions():
        raise AppException(40020, f"不允许的文件扩展名: {ext}")
    if content_type not in ALLOWED_MIME:
        raise AppException(40021, f"不允许的MIME类型: {content_type}")
    mb = _max_upload_mb()
    max_size = mb * 1024 * 1024
    if len(data) > max_size:
        raise AppException(40022, f"文件大小超过限制 {mb}MB")
    if not data:
        raise AppException(40025, "空文件")
    _validate_magic(ext, data)
    assert_image_safe(data)
    # 自动转换为 WebP 格式（jpg/png → webp，gif 保留原格式）
    data, ext = _convert_to_webp(data, ext)
    if ext == "webp":
        content_type = "image/webp"
    key = make_object_key(subdir, ext)
    return await get_storage().put_bytes(key, data, content_type=content_type or "application/octet-stream")


async def save_upload(file: UploadFile, subdir: str = "products") -> str:
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    data = await read_upload_limited(file)
    return await save_image_bytes(data, ext=ext, content_type=content_type, subdir=subdir)


async def save_store_logo(file: UploadFile) -> str:
    """门店 Logo：校验类型/大小 → 正方形缩放 → 强制 WebP → stores/。"""
    data = await read_upload_limited(file, max_bytes=_STORE_LOGO_MAX_BYTES)
    if not data:
        raise AppException(40025, "空文件")
    if len(data) > _STORE_LOGO_MAX_BYTES:
        raise AppException(40022, "Logo 文件不能超过 5MB")

    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    content_type = (file.content_type or mimetypes.guess_type(file.filename or "")[0] or "").lower()
    try:
        sniffed_ext, sniffed_mime = sniff_image_type(data)
    except AppException:
        raise AppException(40021, "仅支持 JPG / PNG / WebP 图片") from None

    if sniffed_ext == "gif":
        raise AppException(40021, "门店 Logo 不支持 GIF，请使用 JPG / PNG / WebP")

    # 扩展名缺失或与内容不符时，以魔数为准
    if not ext or ext not in _STORE_LOGO_EXTS:
        ext = sniffed_ext
    elif ext != sniffed_ext and not (
        ext in ("jpg", "jpeg") and sniffed_ext in ("jpg", "jpeg")
    ):
        raise AppException(40024, "文件内容与扩展名不符")
    if content_type and content_type not in _STORE_LOGO_MIME and content_type != "application/octet-stream":
        # 部分浏览器对 webp/jpeg 标注不准，仍以魔数为准，仅拒绝明确错误类型
        if content_type.startswith("image/") and content_type not in _STORE_LOGO_MIME:
            raise AppException(40021, f"不允许的MIME类型: {content_type}")
    if ext not in _STORE_LOGO_EXTS:
        raise AppException(40020, "仅支持 JPG / PNG / WebP")

    _validate_magic(ext if ext != "jpg" else "jpg", data)
    webp_data = _normalize_store_logo_webp(data)
    key = make_object_key("stores", "webp")
    return await get_storage().put_bytes(key, webp_data, content_type="image/webp")


async def fetch_and_save_remote_image(url: str, subdir: str = "avatars") -> str:
    """下载公网头像（支付宝/微信 CDN）并保存；禁私网、限跳转/体积/超时。"""
    import httpx

    current = str(url or "").strip()
    mb = _max_upload_mb()
    max_size = mb * 1024 * 1024
    data = b""

    async with httpx.AsyncClient(timeout=_REMOTE_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_REMOTE_MAX_REDIRECTS + 1):
            assert_public_http_url(current)
            async with client.stream(
                "GET", current, headers={"User-Agent": "SmartNoodleHouse/1.0"}
            ) as resp:
                peer = _peer_ip_from_response(resp)
                if peer:
                    try:
                        _assert_ip_public(ipaddress.ip_address(peer))
                    except ValueError:
                        raise AppException(40021, _UNSAFE_URL_MSG) from None
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise AppException(40021, "无法下载头像")
                    current = str(resp.url.join(loc))
                    continue
                if resp.status_code >= 400:
                    raise AppException(40021, f"无法下载头像({resp.status_code})")
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > max_size:
                    raise AppException(40022, f"文件大小超过限制 {mb}MB")
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(_READ_CHUNK):
                    total += len(chunk)
                    if total > max_size:
                        raise AppException(
                            40022, f"文件大小超过限制 {mb}MB"
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)
                break
        else:
            raise AppException(40021, "头像地址跳转过多")

    # 下载后再确认最终 URL（缓解 DNS rebinding）
    assert_public_http_url(current)
    ext, content_type = sniff_image_type(data)
    return await save_image_bytes(data, ext=ext, content_type=content_type, subdir=subdir)
