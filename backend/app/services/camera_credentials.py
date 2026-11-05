"""摄像头凭据加密存储"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote, unquote, urlparse, urlunparse

from app.core.exceptions import AppException
from app.services.face_service import FaceEncryption  # 仅用于解密历史摄像头口令
from app.services.secret_encryption import SecretEncryptionService

# P0: 摄像头 URL 禁止指向的元数据/危险端点（允许店内私网 RTSP）
_BLOCKED_HOSTS = {
    "169.254.169.254",  # AWS/云元数据端点
    "metadata.google.internal",
    "metadata",
    "fd00:ec2::254",  # IPv6 元数据
    "100.100.100.200",  # Alibaba Cloud ECS metadata endpoint
    "localhost",
}


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        # IPv4-mapped IPv6（::ffff:a.b.c.d）必须按其映射的 IPv4 地址执行同一策略，
        # 否则 ::ffff:127.0.0.1 / ::ffff:169.254.169.254 可绕过 IPv4 端点拦截。
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return _is_blocked_ip(str(ip.ipv4_mapped))
        # 禁止回环、链路本地、未指定、组播；允许普通私网（店内摄像头）
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
            return True
        if str(ip) in _BLOCKED_HOSTS:
            return True
        if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("169.254.0.0/16"):
            return True
    except ValueError:
        pass
    return False


def assert_camera_url_safe(source_url: str) -> None:
    """校验摄像头 RTSP/HTTP URL 不指向元数据/回环。

    允许 10.x / 172.16-31 / 192.168.x 店内私网。
    对 hostname 做 DNS 解析，拒绝解析到回环/链路本地/元数据（缓解 DNS rebinding）。
    """
    if not source_url or source_url.isdigit():
        return
    try:
        parsed = urlparse(source_url)
        # URL-like 摄像头地址必须是明确支持的协议；解析异常或未知协议不得静默放行。
        if "://" in source_url and parsed.scheme.lower() not in ("rtsp", "rtsps", "http", "https"):
            raise AppException(40026, "摄像头地址协议不受支持")
        if "://" in source_url and not parsed.hostname:
            raise AppException(40026, "摄像头地址缺少有效主机名")
        if "://" in source_url:
            try:
                port = parsed.port
            except ValueError as exc:
                raise AppException(40026, "摄像头地址端口格式非法") from exc
            if port is not None and not (1 <= port <= 65535):
                raise AppException(40026, "摄像头地址端口超出有效范围")
    except AppException:
        raise
    except Exception as exc:
        if "://" in source_url:
            raise AppException(40026, "摄像头地址格式非法") from exc
        return
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return
    if host in _BLOCKED_HOSTS or host.endswith(".localhost") or host.endswith(".local"):
        raise AppException(40026, f"摄像头地址禁止指向元数据/危险端点: {host}")
    if _is_blocked_ip(host):
        raise AppException(40026, "摄像头地址禁止指向回环/链路本地地址")
    # DNS 解析复核：hostname 若解析到危险 IP 则拒绝
    try:
        ipaddress.ip_address(host)
        return  # 已是字面量 IP，上面已校验
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        # 无法解析就无法证明目标安全；拒绝保存，防止 DNS 故障/临时 NXDOMAIN
        # 被当作安全放行，随后主机名解析结果变化指向内网管理面或云元数据。
        raise AppException(40026, f"摄像头主机名无法解析，拒绝连接: {host}") from exc
    if not infos:
        raise AppException(40026, f"摄像头主机名没有有效 DNS 记录: {host}")
    for info in infos:
        addr = info[4][0]
        if _is_blocked_ip(str(addr)):
            raise AppException(40026, f"摄像头地址 DNS 解析到危险地址: {addr}")


def encrypt_password(password: str) -> str:
    """新摄像头口令使用独立 SECRET_ENCRYPTION_KEY 加密。"""
    value = SecretEncryptionService.encrypt(password)
    if value is None:
        raise ValueError("摄像头口令加密失败")
    return value


def decrypt_password(encrypted: str) -> str:
    """优先解密新版本密文；兼容历史 FaceEncryption Fernet 密文。"""
    if SecretEncryptionService.is_encrypted(encrypted):
        value = SecretEncryptionService.decrypt(encrypted)
        if value is None:
            raise ValueError("摄像头口令密文无法解密，请检查 SECRET_ENCRYPTION_KEY")
        return value
    # 历史版本直接使用 FACE_ENCRYPTION_KEY，无版本前缀；只读兼容，后续保存时转为新密钥。
    enc = FaceEncryption()
    return enc.decrypt(encrypted.encode()).decode()


def mask_rtsp_url(source_url: str) -> str:
    """列表展示时隐藏密码"""
    try:
        parsed = urlparse(source_url)
        if parsed.password or (parsed.username and "@" in (parsed.netloc or "")):
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            user = parsed.username or "***"
            netloc = f"{user}:***@{host}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return source_url


def extract_url_credentials(source_url: str) -> tuple[str, str | None, str | None]:
    """
    从 URL 拆出口令并返回无明文密码的 source。
    返回 (safe_url, username, password)。
    """
    if not source_url or source_url.isdigit():
        return source_url, None, None
    try:
        parsed = urlparse(source_url)
    except Exception:
        return source_url, None, None
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return source_url, None, None
    user = unquote(parsed.username) if parsed.username else None
    pwd = unquote(parsed.password) if parsed.password else None
    if not user and not pwd:
        return source_url, None, None
    host = parsed.hostname or ""
    if not host:
        raise AppException(40025, "摄像头地址缺少主机名")
    port = f":{parsed.port}" if parsed.port else ""
    # 禁止把明文密码留在 source_url：仅保留用户名@host（或纯 host）
    if user:
        netloc = f"{quote(user, safe='')}@{host}{port}"
    else:
        netloc = f"{host}{port}"
    safe = urlunparse((parsed.scheme, netloc, parsed.path or "", "", parsed.query, ""))
    return safe, user, pwd


def ensure_no_plaintext_password_in_url(source_url: str) -> None:
    """禁止 rtsp://user:pass@... 明文入库。"""
    if not source_url or "://" not in source_url:
        return
    try:
        parsed = urlparse(source_url)
    except Exception:
        return
    if parsed.password:
        raise AppException(
            40026,
            "禁止在 source_url 中明文保存密码，请将密码填入 password 字段（将加密存储）",
        )


def prepare_camera_source(
    source_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, str | None, str | None]:
    """
    规范化摄像头源：URL 内嵌口令拆到 password_encrypted；source_url 不含明文密码。
    返回 (safe_url, username, password_plaintext_to_encrypt)。
    """
    safe, url_user, url_pwd = extract_url_credentials(source_url)
    # 入库也规范到子码流，避免后台仍显示 101 而实际识别已改连
    safe = prefer_hikvision_substream_url(safe)
    user = (username or url_user or None)
    pwd = password if password is not None and password != "" else url_pwd
    ensure_no_plaintext_password_in_url(safe)
    assert_camera_url_safe(safe)  # P1: SSRF/元数据防护
    # 二次保险：若仍含 :xxx@ 形态则强制剥离
    try:
        parsed = urlparse(safe)
        if parsed.password:
            safe, u2, p2 = extract_url_credentials(safe)
            user = user or u2
            pwd = pwd or p2
    except Exception:
        pass
    ensure_no_plaintext_password_in_url(safe)
    return safe, user, pwd


def build_rtsp_url(host: str, username: str, password: str, channel: str = "102") -> str:
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return f"rtsp://{user}:{pwd}@{host}/Streaming/Channels/{channel}"


def prefer_hikvision_substream_url(source_url: str) -> str:
    """海康主码流 …/Channels/x01 → 子码流 x02，供识别拉流降解码负载。

    仅改写常见主码流尾号 01（101/201/301…）；已是 02/其它通道原样返回。
    USB 数字源、非 Streaming/Channels 路径不改。
    """
    if not source_url or source_url.isdigit():
        return source_url
    try:
        parsed = urlparse(source_url)
    except Exception:
        return source_url
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return source_url
    path = parsed.path or ""
    marker = "/Streaming/Channels/"
    idx = path.find(marker)
    if idx < 0:
        return source_url
    rest = path[idx + len(marker) :]
    channel = rest.split("/", 1)[0].split("?", 1)[0].strip()
    if not channel.isdigit() or len(channel) < 2:
        return source_url
    # 主码流约定：通道号以 01 结尾（101/201…）；子码流为 02
    if not channel.endswith("01"):
        return source_url
    new_channel = channel[:-2] + "02"
    new_path = path[: idx + len(marker)] + new_channel + path[idx + len(marker) + len(channel) :]
    return urlunparse(parsed._replace(path=new_path))


def build_connect_source(
    source_url: str,
    username: str | None,
    password_encrypted: str | None,
) -> str:
    """
    连接时用解密密码重建 RTSP。
    - USB 数字源原样返回
    - 已含明文密码的 URL：优先用加密字段覆盖
    - 仅 host 路径：用 username + 解密密码拼装
    - 海康主码流自动改连子码流（识别用，降 2560 解码）
    """
    if not source_url or source_url.isdigit():
        return source_url
    source_url = prefer_hikvision_substream_url(source_url)
    if not password_encrypted:
        # 无加密密码字段：若 URL 内嵌明文密码则用之（历史脏数据兼容）
        try:
            safe, user, pwd = extract_url_credentials(source_url)
            if pwd:
                parsed = urlparse(safe)
                host = parsed.hostname or ""
                if host and parsed.scheme in ("rtsp", "rtsps", "http", "https"):
                    port = f":{parsed.port}" if parsed.port else ""
                    u = username or user or "admin"
                    netloc = f"{quote(u, safe='')}:{quote(pwd, safe='')}@{host}{port}"
                    return urlunparse((parsed.scheme, netloc, parsed.path or "", "", parsed.query, ""))
        except Exception:
            pass
        return source_url
    try:
        password = decrypt_password(password_encrypted)
    except Exception:
        # P1: 解密失败时 fail-closed — 不回退到可能含明文密码的 source_url
        raise AppException(40026, "摄像头密码解密失败，请重新设置密码")

    parsed = urlparse(source_url)
    if parsed.scheme not in ("rtsp", "rtsps", "http", "https"):
        return source_url

    user = username or parsed.username or "admin"
    host = parsed.hostname or ""
    if not host:
        return source_url
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}{port}"
    return urlunparse((parsed.scheme, netloc, path, "", parsed.query, ""))
