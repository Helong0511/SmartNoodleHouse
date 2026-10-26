"""海康 ISAPI 双向对讲：按摄像头声明的 G.711 µ-law / A-law 推到扬声器。

推流策略对齐 go2rtc / 社区实践：
- close → open → audioData
- audioData 使用 Content-Length: 0，再按略快于实时写 TCP
  （整包/长突发快传会触发固件「只响一下」；纯 1x 又会让长句首声偏慢）
- 编码必须与通道 audioCompressionType 一致（G.711alaw / G.711ulaw）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import socket
import struct
import time
import wave
from io import BytesIO
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# G.711 推流：8kHz 单声道 = 8000 字节/秒
_G711_RATE = 8000
_CHUNK_BYTES = 640  # 80ms/块，减少 syscall；短句可更快出声
_CHUNK_SLEEP = _CHUNK_BYTES / _G711_RATE
# 国内海康门口机多数为 alaw；探测未命中时先用此值，避免堵死首播
_DEFAULT_CODEC = "alaw"
from app.core.hot_cache import HotCache


def tail_hold_seconds(audio_s: float, send_s: float) -> float:
    """节奏推流结束后、close 前应等待的秒数，避免尾句被切断。

    上送常快于实时播放；设备扬声器还有开播延迟。若 hold 不足，
    长句末尾「肠旺面(总店)」会被对讲 close 截掉。
    """
    audio_s = max(0.0, float(audio_s))
    send_s = max(0.0, float(send_s))
    # +1.0：覆盖固件开播延迟；长句再抬一点地板（短试听句不必干等）
    remain = audio_s - send_s + 1.0
    if audio_s >= 4.0:
        remain = max(remain, 1.35)
    elif audio_s >= 2.5:
        remain = max(remain, 0.95)
    elif audio_s < 1.2:
        remain = min(remain, 0.2)
    if remain <= 0.05:
        return 0.08 if audio_s > 0.2 else 0.0
    return min(remain, audio_s + 1.0)


def g711_silence_bytes(codec: str, duration_ms: int) -> bytes:
    """G.711 静音（alaw=0xD5，ulaw=0xFF），用于播完语音后再垫一段再 close。"""
    ms = max(0, int(duration_ms))
    n = int(_G711_RATE * ms / 1000)
    if n <= 0:
        return b""
    if normalize_g711_codec(codec) == "alaw":
        return bytes([0xD5]) * n
    return bytes([0xFF]) * n


# 推流尾部静音：close 落在静音上，避免砍掉「肠旺面，总店」
_G711_TAIL_SILENCE_MS = 1100

# 同摄像头对讲会话互斥（跨 TTSWorker 实例）
_CAMERA_LOCKS: dict[str, asyncio.Lock] = {}
_CAMERA_LOCKS_GUARD = asyncio.Lock()
# 同机复用 Digest 认证状态，避免 open/close 每次新建 Client 再撞 401
# 键含认证指纹，改密后自然错开；仍须调用 invalidate_hikvision_caches 关掉旧 Client
_SHARED_HTTP: dict[str, httpx.AsyncClient] = {}
_SHARED_HTTP_GUARD = asyncio.Lock()
# 通道编码热缓存，避免每次试听都多一轮 Digest GET（仅存探测成功的值）
_CODEC_CACHE: HotCache[str, str] = HotCache("hikvision.codec", max_size=32, ttl_seconds=3600.0)
# 后台探测进行中，避免 resolve_codec(block=False) 反复 create_task
_CODEC_DETECT_PENDING: set[str] = set()
# 上一轮已正常 close 则跳过本轮预 close（省一次 Digest 往返，约 0.5–1s）
_SESSION_NEED_CLOSE: dict[str, bool] = {}
# audioData Digest 挑战缓存（同机短时复用，少一次 401 往返）
_DIGEST_WWW_CACHE: HotCache[str, str] = HotCache("hikvision.digest_www", max_size=32, ttl_seconds=120.0)


def _device_lock_key(base_url: str, channel: int) -> str:
    return f"{base_url.rstrip('/')}|{int(channel)}"


def _auth_fingerprint(username: str, password: str) -> str:
    raw = f"{username or ''}\0{password or ''}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


async def invalidate_hikvision_caches(*, base_url: str, channel: int | None = None) -> None:
    """改密/改地址后关闭共享 HTTP，并清 codec / Digest 挑战缓存。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return
    prefix = f"{base}|"
    to_close: list[httpx.AsyncClient] = []
    async with _SHARED_HTTP_GUARD:
        for key, client in list(_SHARED_HTTP.items()):
            if not key.startswith(prefix):
                continue
            if channel is not None:
                parts = key.split("|")
                if len(parts) < 2 or parts[1] != str(int(channel)):
                    continue
            _SHARED_HTTP.pop(key, None)
            to_close.append(client)
    for client in to_close:
        try:
            await client.aclose()
        except Exception:
            logger.debug("close hikvision shared client failed", exc_info=True)

    with _CODEC_CACHE._lock:
        codec_keys = [
            k
            for k in list(_CODEC_CACHE._data.keys())
            if str(k).startswith(prefix)
            and (
                channel is None
                or (len(str(k).split("|")) >= 2 and str(k).split("|")[1] == str(int(channel)))
            )
        ]
    if channel is not None:
        codec_keys.append(_device_lock_key(base, int(channel)))
    elif not codec_keys:
        codec_keys.append(_device_lock_key(base, 1))
    for ck in set(codec_keys):
        _CODEC_CACHE.delete(ck)
        _CODEC_DETECT_PENDING.discard(str(ck))
        _SESSION_NEED_CLOSE.pop(str(ck), None)
        _DIGEST_WWW_CACHE.delete(str(ck))

    try:
        from app.core.cache_keys import hikvision_codec_key
        from app.core.redis_client import get_redis

        redis = await get_redis()
        chans = [int(channel)] if channel is not None else sorted(
            {
                int(str(k).split("|")[1])
                for k in codec_keys
                if len(str(k).split("|")) >= 2 and str(k).split("|")[1].isdigit()
            }
            or {1}
        )
        for c in chans:
            await redis.delete(hikvision_codec_key(base, c))
    except Exception:
        logger.debug("delete hikvision codec redis key failed", exc_info=True)


def resolve_isapi_base(source_url: str) -> str | None:
    """从摄像头 source_url 推导 ISAPI HTTP 根地址（RTSP 主机 → http://host）。"""
    raw = (source_url or "").strip()
    if not raw or raw.isdigit():
        return None
    if "://" not in raw:
        raw = f"rtsp://{raw}"
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    if parsed.scheme in ("http", "https"):
        port = f":{parsed.port}" if parsed.port else ""
        # 保留调用方 scheme；play_g711 对 https 显式拒绝（无 TLS 推流）
        return f"{parsed.scheme}://{host}{port}"
    # RTSP/其他：对讲 ISAPI 默认走 HTTP（非 TLS 明文 TCP audioData）
    return f"http://{host}"


# 海康通道名 / OSD 名称常见上限 32 字节（UTF-8）
_OSD_NAME_MAX_BYTES = 32


def clip_osd_channel_name(name: str, *, max_bytes: int = _OSD_NAME_MAX_BYTES) -> str:
    """裁剪到设备可接受长度，避免中文名 PUT 失败。"""
    raw = (name or "").strip()
    if not raw:
        return ""
    encoded = raw.encode("utf-8")
    if len(encoded) <= max_bytes:
        return raw
    out = bytearray()
    for ch in raw:
        b = ch.encode("utf-8")
        if len(out) + len(b) > max_bytes:
            break
        out.extend(b)
    return out.decode("utf-8", errors="ignore")


def _xml_set_tag_text(xml_text: str, tag: str, value: str) -> str:
    """在已有 ISAPI XML 中替换首个同名标签正文（保留其余字段）。"""
    pattern = re.compile(
        rf"(<{re.escape(tag)}(?:\s[^>]*)?>)(.*?)(</{re.escape(tag)}>)",
        re.DOTALL | re.IGNORECASE,
    )
    escaped = (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    new_xml, n = pattern.subn(rf"\g<1>{escaped}\g<3>", xml_text, count=1)
    if n == 0:
        raise ValueError(f"XML 缺少 <{tag}>")
    return new_xml


async def sync_channel_osd_name(
    base_url: str,
    username: str,
    password: str,
    display_name: str,
    *,
    channel: int = 1,
) -> dict:
    """把后台摄像头名称写到海康右下角通道名 OSD（channelNameOverlay）。

    同时更新 VideoInputChannel.name，与预览角标保持一致。
    """
    name = clip_osd_channel_name(display_name)
    if not name:
        return {"ok": False, "reason": "empty_name"}
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "reason": "no_base"}
    ch = max(1, int(channel or 1))
    overlay_path = f"/ISAPI/System/Video/inputs/channels/{ch}/overlays/channelNameOverlay"
    channel_path = f"/ISAPI/System/Video/inputs/channels/{ch}"
    auth = httpx.DigestAuth(username or "admin", password or "")
    timeout = httpx.Timeout(8.0, connect=3.0)
    async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=False) as client:
        ov = await client.get(f"{base}{overlay_path}")
        ov.raise_for_status()
        ov_xml = _xml_set_tag_text(ov.text or "", "name", name)
        # 确保 OSD 开启，否则改名也看不见
        if re.search(r"<enabled>\s*false\s*</enabled>", ov_xml, re.I):
            ov_xml = re.sub(
                r"<enabled>\s*false\s*</enabled>",
                "<enabled>true</enabled>",
                ov_xml,
                count=1,
                flags=re.I,
            )
        put_ov = await client.put(
            f"{base}{overlay_path}",
            content=ov_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=UTF-8"},
        )
        put_ov.raise_for_status()

        ch_resp = await client.get(f"{base}{channel_path}")
        if ch_resp.status_code == 200 and ch_resp.text:
            try:
                ch_xml = _xml_set_tag_text(ch_resp.text, "name", name)
                put_ch = await client.put(
                    f"{base}{channel_path}",
                    content=ch_xml.encode("utf-8"),
                    headers={"Content-Type": "application/xml; charset=UTF-8"},
                )
                put_ch.raise_for_status()
            except Exception:
                logger.debug("update VideoInputChannel name skipped", exc_info=True)

    logger.info(
        "海康通道名 OSD 已同步 channel=%s name=%s base=%s",
        ch,
        name,
        base,
    )
    return {"ok": True, "name": name, "channel": ch}


async def try_sync_camera_osd_name(
    *,
    source_url: str,
    username: str | None,
    password_encrypted: str | None,
    display_name: str,
) -> dict:
    """最佳努力：USB/无 ISAPI 基址时跳过；失败只记日志不抛给前端。"""
    base = resolve_isapi_base(source_url)
    if not base:
        return {"ok": False, "reason": "not_isapi"}
    try:
        from app.services.camera_credentials import decrypt_password

        pwd = decrypt_password(password_encrypted) if password_encrypted else ""
    except Exception:
        pwd = ""
    try:
        return await sync_channel_osd_name(
            base,
            username or "admin",
            pwd,
            display_name,
            channel=1,
        )
    except Exception as e:
        logger.warning(
            "同步海康通道名 OSD 失败 base=%s name=%s err=%s",
            base,
            (display_name or "")[:40],
            str(e)[:120],
        )
        return {"ok": False, "reason": "isapi_error", "error": str(e)[:200]}


def _lin2xlaw(pcm: bytes, *, law: str) -> bytes:
    """16-bit LE PCM → G.711；优先 stdlib audioop（Py3.11），否则纯 Python 回退。"""
    if not pcm:
        return b""
    try:
        import audioop  # type: ignore

        if law == "alaw":
            return audioop.lin2alaw(pcm, 2)
        return audioop.lin2ulaw(pcm, 2)
    except Exception:
        pass
    out = bytearray(len(pcm) // 2)
    for i in range(0, len(pcm) - 1, 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        out[i // 2] = (
            _linear16_to_alaw_fallback(sample)
            if law == "alaw"
            else _linear16_to_ulaw_fallback(sample)
        )
    return bytes(out)


def _linear16_to_ulaw_fallback(sample: int) -> int:
    bias, clip = 0x84, 32635
    exp_lut = (
        [0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3]
        + [4] * 16
        + [5] * 32
        + [6] * 64
        + [7] * 128
    )
    sample = max(-32768, min(32767, int(sample)))
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    if sample > clip:
        sample = clip
    sample = sample + bias
    exponent = exp_lut[(sample >> 7) & 0xFF]
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _linear16_to_alaw_fallback(sample: int) -> int:
    seg_end = (0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF)
    pcm_val = max(-32768, min(32767, int(sample))) >> 2
    if pcm_val >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm_val = -pcm_val - 8
    seg = 7
    for i, end in enumerate(seg_end):
        if pcm_val <= end:
            seg = i
            break
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << 4
    if seg < 2:
        aval |= (pcm_val >> 4) & 0x0F
    else:
        aval |= (pcm_val >> (seg + 3)) & 0x0F
    return aval ^ mask


def linear16_to_ulaw(sample: int) -> int:
    return _lin2xlaw(struct.pack("<h", int(sample)), law="ulaw")[0]


def linear16_to_alaw(sample: int) -> int:
    return _lin2xlaw(struct.pack("<h", int(sample)), law="alaw")[0]


def pcm16le_to_ulaw(pcm: bytes) -> bytes:
    return _lin2xlaw(pcm, law="ulaw")


def pcm16le_to_alaw(pcm: bytes) -> bytes:
    return _lin2xlaw(pcm, law="alaw")


def resample_pcm16_mono(pcm: bytes, src_rate: int, dst_rate: int = _G711_RATE) -> bytes:
    if src_rate <= 0:
        raise ValueError("invalid sample rate")
    if src_rate == dst_rate:
        return pcm
    n_src = len(pcm) // 2
    if n_src == 0:
        return b""
    n_dst = max(1, int(n_src * dst_rate / src_rate))
    out = bytearray(n_dst * 2)
    for i in range(n_dst):
        src_pos = i * (n_src - 1) / max(1, n_dst - 1)
        i0 = int(src_pos)
        i1 = min(i0 + 1, n_src - 1)
        frac = src_pos - i0
        s0 = struct.unpack_from("<h", pcm, i0 * 2)[0]
        s1 = struct.unpack_from("<h", pcm, i1 * 2)[0]
        sample = int(s0 + (s1 - s0) * frac)
        struct.pack_into("<h", out, i * 2, max(-32768, min(32767, sample)))
    return bytes(out)


def scale_pcm16_volume(pcm: bytes, volume_percent: int) -> bytes:
    try:
        pct = int(volume_percent)
    except (TypeError, ValueError):
        pct = 100
    pct = max(0, min(150, pct))
    if pct == 100 or not pcm:
        return pcm
    gain = pct / 100.0
    out = bytearray(len(pcm))
    for i in range(0, len(pcm) - 1, 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        scaled = int(sample * gain)
        struct.pack_into("<h", out, i, max(-32768, min(32767, scaled)))
    return bytes(out)


def _wav_to_pcm8k(wav_data: bytes, *, volume_percent: int = 100) -> bytes:
    with wave.open(BytesIO(wav_data), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise ValueError(f"仅支持 16-bit PCM WAV，当前 sampwidth={sampwidth}")
    if channels > 1:
        mono = bytearray()
        for i in range(0, len(frames), sampwidth * channels):
            mono.extend(frames[i : i + 2])
        frames = bytes(mono)
    pcm8k = resample_pcm16_mono(frames, rate, _G711_RATE)
    return scale_pcm16_volume(pcm8k, volume_percent)


def normalize_g711_codec(raw: str | None) -> str:
    s = (raw or "").strip().lower().replace(" ", "").replace("_", "").replace(".", "")
    if "alaw" in s or s in ("g711a", "pcma"):
        return "alaw"
    return "ulaw"


def wav_bytes_to_g711_8k(
    wav_data: bytes,
    *,
    codec: str = "ulaw",
    volume_percent: int = 100,
) -> bytes:
    pcm8k = _wav_to_pcm8k(wav_data, volume_percent=volume_percent)
    if normalize_g711_codec(codec) == "alaw":
        return pcm16le_to_alaw(pcm8k)
    return pcm16le_to_ulaw(pcm8k)


def wav_bytes_to_ulaw8k(wav_data: bytes, *, volume_percent: int = 100) -> bytes:
    return wav_bytes_to_g711_8k(wav_data, codec="ulaw", volume_percent=volume_percent)


def _xml_local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _parse_session_id(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for el in root.iter():
        if _xml_local(el.tag) == "sessionId" and (el.text or "").strip():
            return el.text.strip()
    return None


def _parse_channel_audio(xml_text: str, channel: int) -> tuple[str, bool]:
    codec = "ulaw"
    enabled = True
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return codec, enabled
    for ch in root.iter():
        if _xml_local(ch.tag) != "TwoWayAudioChannel":
            continue
        cid = None
        ctype = None
        cen = None
        for child in list(ch):
            name = _xml_local(child.tag)
            text = (child.text or "").strip()
            if name == "id":
                cid = text
            elif name == "audioCompressionType":
                ctype = text
            elif name == "enabled":
                cen = text.lower() in ("true", "1", "yes")
        if cid is not None and str(cid) != str(channel):
            continue
        if ctype:
            codec = normalize_g711_codec(ctype)
        if cen is not None:
            enabled = cen
        break
    return codec, enabled


def _md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _build_digest_authorization(
    www_authenticate: str,
    *,
    method: str,
    uri: str,
    username: str,
    password: str,
) -> str:
    """构造 HTTP Digest Authorization（兼容 Hikvision qop=auth）。"""
    realm_m = re.search(r'realm="([^"]+)"', www_authenticate, re.I)
    nonce_m = re.search(r'nonce="([^"]+)"', www_authenticate, re.I)
    qop_m = re.search(r"qop=\"([^\"]+)\"", www_authenticate, re.I)
    opaque_m = re.search(r'opaque="([^"]+)"', www_authenticate, re.I)
    realm = realm_m.group(1) if realm_m else ""
    nonce = nonce_m.group(1) if nonce_m else ""
    ha1 = _md5_hex(f"{username}:{realm}:{password}")
    ha2 = _md5_hex(f"{method}:{uri}")
    if qop_m and "auth" in qop_m.group(1).lower():
        cnonce = hashlib.md5(os.urandom(8)).hexdigest()
        nc = "00000001"
        resp = _md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        parts = [
            f'username="{username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            f'response="{resp}"',
            "qop=auth",
            f"nc={nc}",
            f'cnonce="{cnonce}"',
        ]
        if opaque_m:
            parts.append(f'opaque="{opaque_m.group(1)}"')
        return "Digest " + ", ".join(parts)
    resp = _md5_hex(f"{ha1}:{nonce}:{ha2}")
    return (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{resp}"'
    )


def _read_http_response(sock: socket.socket) -> tuple[int, dict[str, str], bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    header_raw, _, rest = buf.partition(b"\r\n\r\n")
    lines = header_raw.decode("latin-1", errors="replace").split("\r\n")
    status = 0
    if lines:
        parts = lines[0].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    hdrs: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    return status, hdrs, rest


def _stream_audio_data_content_length_zero(
    *,
    host: str,
    port: int,
    path: str,
    username: str,
    password: str,
    g711_data: bytes,
    should_abort=None,
) -> dict[str, int]:
    """go2rtc 同款：PUT audioData + Content-Length:0，再写 TCP 正文。

    仅前 ~240ms 零休眠启动扬声器；其后按约 1.5–2x 节奏推送。
    日志曾证实：前 1.2s 整段突发时短句「试听播报」固件只出「试」一声。
    返回 first_chunk_ms / send_ms 供上层日志。
    should_abort：可选无参回调，返回 True 时中断推流（会员打断陌生迎宾）。
    """
    def _one_shot(auth_header: str | None) -> tuple[socket.socket, int, dict[str, str]]:
        sock = socket.create_connection((host, port), timeout=5.0)
        sock.settimeout(5.0)
        lines = [
            f"PUT {path} HTTP/1.1",
            f"Host: {host if port in (80, 443) else f'{host}:{port}'}",
            "Content-Type: application/octet-stream",
            "Content-Length: 0",
            "Connection: keep-alive",
        ]
        if auth_header:
            lines.append(f"Authorization: {auth_header}")
        payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        sock.sendall(payload)
        status, hdrs, _ = _read_http_response(sock)
        return sock, status, hdrs

    t_start = time.perf_counter()
    dur = len(g711_data) / float(_G711_RATE)
    # 短句略慢于实时，防「试听播报」固件只出「试」；中长句再略加速首声
    if dur <= 2.5:
        pace = 0.72
    elif dur <= 3.5:
        pace = 0.58
    elif dur <= 8.0:
        pace = 0.50
    else:
        pace = 0.62
    chunk_sleep = _CHUNK_SLEEP * pace
    # 最多 2 块（约 160ms）零休眠；3 块短句仍易被固件截成一声
    burst_bytes = min(len(g711_data), _CHUNK_BYTES * 2)
    digest_key = f"{host}:{port}|{path}|{username}"

    # 1) Digest：优先用缓存的 WWW-Authenticate，省掉首包 401
    www_cached = _DIGEST_WWW_CACHE.get(digest_key)
    auth_header = None
    if www_cached:
        auth_header = _build_digest_authorization(
            www_cached, method="PUT", uri=path, username=username, password=password
        )
    sock, status, hdrs = _one_shot(auth_header)
    first_chunk_ms = -1
    aborted = False
    try:
        if status == 401:
            www = hdrs.get("www-authenticate") or ""
            if www:
                _DIGEST_WWW_CACHE.put(digest_key, www)
            auth = _build_digest_authorization(
                www, method="PUT", uri=path, username=username, password=password
            )
            sock.close()
            sock, status, hdrs = _one_shot(auth)
        # status==0 常见于对端无完整响应头，不得当作可靠成功；仅允许 100/200 继续推流
        if status == 401:
            raise RuntimeError("audioData Digest 认证失败 HTTP 401")
        if status not in (200, 100):
            if status >= 400 or status == 0:
                raise RuntimeError(
                    f"audioData 握手失败 HTTP {status}（禁止将 status=0 视为成功）"
                )
        # 2) 极短启动突发 + 受控节奏（防固件只响一声）
        aborted = False
        for i in range(0, len(g711_data), _CHUNK_BYTES):
            if should_abort is not None:
                try:
                    if should_abort():
                        aborted = True
                        break
                except Exception:
                    pass
            sock.sendall(g711_data[i : i + _CHUNK_BYTES])
            if first_chunk_ms < 0:
                first_chunk_ms = int((time.perf_counter() - t_start) * 1000)
            end = i + _CHUNK_BYTES
            if end < burst_bytes:
                continue
            if chunk_sleep > 0 and end < len(g711_data):
                time.sleep(chunk_sleep)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            sock.settimeout(1.5)
            _read_http_response(sock)
        except OSError:
            pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    send_ms = int((time.perf_counter() - t_start) * 1000)
    return {
        "first_chunk_ms": max(0, first_chunk_ms),
        "send_ms": send_ms,
        "audio_ms": int(dur * 1000),
        "aborted": 1 if aborted else 0,
    }


class HikvisionTwoWayAudio:
    """对一台海康摄像头打开对讲通道并按设备编码推送 G.711。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        channel: int = 1,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.channel = int(channel)
        self.timeout = timeout

    async def _lock_for(self) -> asyncio.Lock:
        key = _device_lock_key(self.base_url, self.channel)
        async with _CAMERA_LOCKS_GUARD:
            lock = _CAMERA_LOCKS.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _CAMERA_LOCKS[key] = lock
            return lock

    def _url(self, suffix: str = "") -> str:
        return f"{self.base_url}/ISAPI/System/TwoWayAudio/channels/{self.channel}{suffix}"

    def _device_key(self) -> str:
        """设备维度键（与账号无关）：codec / 对讲锁 / session 标志。"""
        return _device_lock_key(self.base_url, self.channel)

    def _client_key(self) -> str:
        """含认证指纹，改密后不会误复用旧 Digest Client。"""
        fp = _auth_fingerprint(self.username, self.password)
        return f"{self._device_key()}|{fp}"

    async def _shared_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """按摄像头+凭证复用 AsyncClient，保留 Digest 协商结果，缩短 open 首包。"""
        key = self._client_key()
        async with _SHARED_HTTP_GUARD:
            client = _SHARED_HTTP.get(key)
            if client is None or getattr(client, "is_closed", False):
                client = httpx.AsyncClient(
                    auth=httpx.DigestAuth(self.username, self.password),
                    timeout=timeout,
                    follow_redirects=False,
                )
                _SHARED_HTTP[key] = client
            else:
                try:
                    client.timeout = timeout
                except Exception:
                    pass
            return client

    async def _persist_codec(self, codec: str) -> None:
        key = self._device_key()
        c = normalize_g711_codec(codec)
        _CODEC_CACHE.put(key, c)
        try:
            from app.core.cache_keys import hikvision_codec_key
            from app.core.redis_client import get_redis

            redis = await get_redis()
            await redis.set(hikvision_codec_key(self.base_url, self.channel), c, ex=7 * 24 * 3600)
        except Exception:
            logger.debug("persist hikvision codec failed", exc_info=True)

    async def _detect_codec(self, client: httpx.AsyncClient) -> str:
        try:
            resp = await client.get(f"{self.base_url}/ISAPI/System/TwoWayAudio/channels")
            if resp.status_code == 200 and resp.text:
                codec, enabled = _parse_channel_audio(resp.text, self.channel)
                await self._persist_codec(codec)
                logger.info(
                    "海康对讲通道 codec=%s enabled=%s base=%s channel=%s",
                    codec,
                    enabled,
                    self.base_url,
                    self.channel,
                )
                return normalize_g711_codec(codec)
            logger.warning(
                "hikvision detect codec HTTP %s，不持久化默认值 base=%s",
                getattr(resp, "status_code", "?"),
                self.base_url,
            )
        except Exception:
            logger.debug("hikvision detect codec failed, ephemeral fallback %s", _DEFAULT_CODEC, exc_info=True)
        # 探测失败：仅本次返回默认值，禁止写入内存/Redis，以免真机 ulaw 被锁死 7 天
        return _DEFAULT_CODEC

    async def resolve_codec(self, *, block: bool = False) -> str:
        """解析 G.711 编码。

        默认不阻塞：内存/Redis 未命中时立即返回临时 alaw（不入缓存），后台再探测；
        探测成功后才持久化。热重载后首播若同步探测，曾出现 codec_ms>15s。
        """
        key = self._device_key()
        cached = _CODEC_CACHE.get(key)
        if cached:
            return cached
        try:
            from app.core.cache_keys import hikvision_codec_key
            from app.core.redis_client import get_redis

            redis = await get_redis()
            raw = await redis.get(hikvision_codec_key(self.base_url, self.channel))
            if raw:
                c = normalize_g711_codec(
                    raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                )
                _CODEC_CACHE.put(key, c)
                return c
        except Exception:
            logger.debug("redis hikvision codec read failed", exc_info=True)

        if not block:
            if key not in _CODEC_DETECT_PENDING:
                _CODEC_DETECT_PENDING.add(key)

                async def _bg() -> None:
                    try:
                        timeout = httpx.Timeout(8.0, connect=3.0)
                        client = await self._shared_client(timeout)
                        await self._detect_codec(client)
                    except Exception:
                        logger.debug("bg hikvision codec detect failed", exc_info=True)
                    finally:
                        _CODEC_DETECT_PENDING.discard(key)

                try:
                    asyncio.get_running_loop().create_task(_bg())
                except RuntimeError:
                    _CODEC_DETECT_PENDING.discard(key)
            return _DEFAULT_CODEC

        timeout = httpx.Timeout(8.0, connect=3.0)
        client = await self._shared_client(timeout)
        return await self._detect_codec(client)

    async def play_g711(
        self,
        g711_data: bytes,
        *,
        codec: str = "ulaw",
        should_abort=None,
    ) -> dict:
        """推送 G.711 到摄像头对讲。

        返回结构化结果（不得把「字节已发送」等同于「扬声器已出声」）：
        - status: completed | cancelled | failed
        - device_playback: unverified（本链路无可靠播放完成 ACK）
        - error: 可选错误码，如 unsupported_audio_transport
        """
        if not g711_data:
            return {
                "status": "failed",
                "error": "empty_payload",
                "device_playback": "unverified",
            }
        codec = normalize_g711_codec(codec)
        timeout = httpx.Timeout(
            max(float(self.timeout), len(g711_data) / float(_G711_RATE) + 20.0),
            connect=5.0,
        )
        parsed = urlparse(self.base_url)
        scheme = (parsed.scheme or "http").lower()
        # 方案 A：原始 TCP audioData 无 TLS；HTTPS 基址明确拒绝，禁止静默当 HTTP
        if scheme == "https":
            logger.error(
                "海康对讲拒绝 HTTPS 基址（原始 TCP 推流无 TLS）base=%s",
                self.base_url,
            )
            return {
                "status": "failed",
                "error": "unsupported_audio_transport",
                "device_playback": "unverified",
                "detail": "audioData uses plain TCP; configure HTTP ISAPI base",
            }
        host = parsed.hostname or ""
        port = int(parsed.port or 80)
        if not host:
            return {
                "status": "failed",
                "error": "invalid_base_url",
                "device_playback": "unverified",
            }

        lock = await self._lock_for()
        key = self._device_key()
        async with lock:
            if should_abort is not None:
                try:
                    if should_abort():
                        logger.info("海康对讲播报在 open 前已取消 base=%s", self.base_url)
                        return {
                            "status": "cancelled",
                            "phase": "before_open",
                            "device_playback": "unverified",
                        }
                except Exception:
                    pass
            client = await self._shared_client(timeout)
            t_open = time.perf_counter()
            # 已知空闲则跳过预 close，少一轮 Digest（常见省 0.5–1s）
            if _SESSION_NEED_CLOSE.get(key, False):
                try:
                    await client.put(self._url("/close"))
                except Exception:
                    logger.debug("hikvision close stale session ignored", exc_info=True)
                finally:
                    _SESSION_NEED_CLOSE[key] = False

            try:
                open_resp = await client.put(self._url("/open"))
                open_resp.raise_for_status()
            except Exception as e:
                logger.warning("海康对讲 open 失败 base=%s err=%s", self.base_url, e)
                return {
                    "status": "failed",
                    "error": "open_failed",
                    "device_playback": "unverified",
                    "detail": str(e)[:160],
                }
            open_ms = int((time.perf_counter() - t_open) * 1000)
            _SESSION_NEED_CLOSE[key] = True
            session_id = _parse_session_id(open_resp.text) or ""
            path = f"/ISAPI/System/TwoWayAudio/channels/{self.channel}/audioData"
            if session_id:
                path = f"{path}?sessionId={session_id}"

            t0 = time.perf_counter()
            # 语音时长按「有效语音」算；会员长句尾部再垫静音，close 落在静音段
            speech_s = len(g711_data) / float(_G711_RATE)
            silence = b""
            if (
                _G711_TAIL_SILENCE_MS > 0
                and should_abort is None
                and speech_s >= 2.0
            ):
                silence = g711_silence_bytes(codec, _G711_TAIL_SILENCE_MS)
            stream_data = g711_data + silence if silence else g711_data
            audio_s = len(stream_data) / float(_G711_RATE)
            aborted = False
            stats: dict = {}
            try:
                # CL:0 + 受控节奏：过快突发会「只响试」；过慢则长句首声拖很久
                stats = await asyncio.to_thread(
                    _stream_audio_data_content_length_zero,
                    host=host,
                    port=port,
                    path=path,
                    username=self.username,
                    password=self.password,
                    g711_data=stream_data,
                    should_abort=should_abort,
                )
                if not isinstance(stats, dict):
                    stats = {}
                send_s = time.perf_counter() - t0
                aborted = int(stats.get("aborted") or 0) == 1
                # 节奏推流常快于实时：close 太早会砍掉尾句（如「肠旺面(总店)」）。
                # hold 按有效语音时长；尾静音再吃掉一截误差。打断时跳过。
                remain = 0.0
                if not aborted:
                    # 已上送的静音可抵消部分 hold，但仍按 speech 留足店名
                    silence_s = len(silence) / float(_G711_RATE)
                    effective_send = max(0.0, send_s - silence_s * 0.85)
                    remain = tail_hold_seconds(speech_s, effective_send)
                    if remain > 0.05:
                        await asyncio.sleep(remain)
                logger.info(
                    "海康对讲节奏推流完成 codec=%s bytes=%s speech_ms=%s silence_ms=%s "
                    "open_ms=%s first_chunk_ms=%s send_ms=%s push_ms=%s audio_ms=%s "
                    "remain_ms=%s aborted=%s device_playback=unverified",
                    codec,
                    len(stream_data),
                    int(speech_s * 1000),
                    int(len(silence) / float(_G711_RATE) * 1000),
                    open_ms,
                    stats.get("first_chunk_ms", -1),
                    stats.get("send_ms", int(send_s * 1000)),
                    int((time.perf_counter() - t0) * 1000),
                    stats.get("audio_ms", int(audio_s * 1000)),
                    int(remain * 1000),
                    aborted,
                )
            except Exception as e:
                logger.warning("海康对讲推流失败 base=%s err=%s", self.base_url, e)
                return {
                    "status": "failed",
                    "error": "stream_failed",
                    "device_playback": "unverified",
                    "detail": str(e)[:160],
                    "open_ms": open_ms,
                }
            finally:
                try:
                    await client.put(self._url("/close"))
                    _SESSION_NEED_CLOSE[key] = False
                except Exception:
                    logger.debug("hikvision close after play ignored", exc_info=True)
                    _SESSION_NEED_CLOSE[key] = True
                await asyncio.sleep(0.08)

            if aborted:
                return {
                    "status": "cancelled",
                    "phase": "sending",
                    "device_playback": "unverified",
                    "open_ms": open_ms,
                    "first_chunk_ms": stats.get("first_chunk_ms", -1),
                    "send_ms": stats.get("send_ms"),
                }
            return {
                "status": "completed",
                "device_playback": "unverified",
                "open_ms": open_ms,
                "first_chunk_ms": stats.get("first_chunk_ms", -1),
                "send_ms": stats.get("send_ms"),
                "note": "bytes_sent_not_speaker_ack",
            }

    async def play_ulaw(self, ulaw_data: bytes) -> None:
        await self.play_g711(ulaw_data, codec="ulaw")

    async def play_wav(self, wav_data: bytes, *, volume_percent: int = 100) -> str:
        timeout = httpx.Timeout(15.0, connect=5.0)
        client = await self._shared_client(timeout)
        codec = await self._detect_codec(client)
        g711 = await asyncio.to_thread(
            wav_bytes_to_g711_8k,
            wav_data,
            codec=codec,
            volume_percent=volume_percent,
        )
        await self.play_g711(g711, codec=codec)
        return codec
