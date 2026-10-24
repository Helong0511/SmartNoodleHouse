"""海康 ISAPI 双向对讲：按摄像头声明的 G.711 µ-law / A-law 推到扬声器。

推流策略对齐 go2rtc / 社区实践：
- close → open → audioData
- audioData 使用 Content-Length: 0，再按 8kHz 实时写 TCP（整包快传会导致只响一下/偶发无声）
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

# G.711 实时推流：8kHz 单声道 = 8000 字节/秒
_G711_RATE = 8000
_CHUNK_BYTES = 160  # 20ms
_CHUNK_SLEEP = _CHUNK_BYTES / _G711_RATE
from app.core.hot_cache import HotCache

# 同摄像头对讲会话互斥（跨 TTSWorker 实例）
_CAMERA_LOCKS: dict[str, asyncio.Lock] = {}
_CAMERA_LOCKS_GUARD = asyncio.Lock()
# 通道编码热缓存，避免每次试听都多一轮 Digest GET（~0.7s）
_CODEC_CACHE: HotCache[str, str] = HotCache("hikvision.codec", max_size=32, ttl_seconds=600.0)


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
        return f"{parsed.scheme}://{host}{port}"
    return f"http://{host}"


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
) -> None:
    """go2rtc 同款：PUT audioData + Content-Length:0，再按实时节奏写 TCP 正文。"""
    def _one_shot(auth_header: str | None) -> tuple[socket.socket, int, dict[str, str]]:
        sock = socket.create_connection((host, port), timeout=8.0)
        sock.settimeout(8.0)
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

    # 1) 拿 Digest challenge
    sock, status, hdrs = _one_shot(None)
    try:
        if status == 401:
            www = hdrs.get("www-authenticate") or ""
            auth = _build_digest_authorization(
                www, method="PUT", uri=path, username=username, password=password
            )
            sock.close()
            sock, status, hdrs = _one_shot(auth)
        if status not in (0, 200, 100) and status != 401:
            # 部分固件对 CL:0 不立即回包，status=0 表示还没响应，可继续写
            if status >= 400:
                raise RuntimeError(f"audioData 握手失败 HTTP {status}")
        # 2) 实时写音频（非法 HTTP，但是海康对讲约定）
        for i in range(0, len(g711_data), _CHUNK_BYTES):
            sock.sendall(g711_data[i : i + _CHUNK_BYTES])
            time.sleep(_CHUNK_SLEEP)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            sock.settimeout(2.0)
            _read_http_response(sock)
        except OSError:
            pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


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
        key = f"{self.base_url}|{self.channel}"
        async with _CAMERA_LOCKS_GUARD:
            lock = _CAMERA_LOCKS.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _CAMERA_LOCKS[key] = lock
            return lock

    def _url(self, suffix: str = "") -> str:
        return f"{self.base_url}/ISAPI/System/TwoWayAudio/channels/{self.channel}{suffix}"

    def _cache_key(self) -> str:
        return f"{self.base_url}|{self.channel}"

    async def _detect_codec(self, client: httpx.AsyncClient) -> str:
        key = self._cache_key()
        cached = _CODEC_CACHE.get(key)
        if cached:
            return cached
        try:
            resp = await client.get(f"{self.base_url}/ISAPI/System/TwoWayAudio/channels")
            if resp.status_code == 200 and resp.text:
                codec, enabled = _parse_channel_audio(resp.text, self.channel)
                _CODEC_CACHE.put(key, codec)
                logger.info(
                    "海康对讲通道 codec=%s enabled=%s base=%s channel=%s",
                    codec,
                    enabled,
                    self.base_url,
                    self.channel,
                )
                return codec
        except Exception:
            logger.debug("hikvision detect codec failed, fallback ulaw", exc_info=True)
        return "ulaw"

    async def resolve_codec(self) -> str:
        """读取（或探测并缓存）摄像头 G.711 编码。"""
        cached = _CODEC_CACHE.get(self._cache_key())
        if cached:
            return cached
        auth = httpx.DigestAuth(self.username, self.password)
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=False) as client:
            return await self._detect_codec(client)

    async def play_g711(self, g711_data: bytes, *, codec: str = "ulaw") -> None:
        if not g711_data:
            return
        codec = normalize_g711_codec(codec)
        auth = httpx.DigestAuth(self.username, self.password)
        timeout = httpx.Timeout(
            max(float(self.timeout), len(g711_data) / float(_G711_RATE) + 20.0),
            connect=5.0,
        )
        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        if not host:
            raise RuntimeError("invalid ISAPI base url")

        lock = await self._lock_for()
        async with lock:
            async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=False) as client:
                try:
                    await client.put(self._url("/close"))
                except Exception:
                    logger.debug("hikvision close stale session ignored", exc_info=True)

                open_resp = await client.put(self._url("/open"))
                open_resp.raise_for_status()
                session_id = _parse_session_id(open_resp.text) or ""
                path = f"/ISAPI/System/TwoWayAudio/channels/{self.channel}/audioData"
                if session_id:
                    path = f"{path}?sessionId={session_id}"

                t0 = time.perf_counter()
                try:
                    # 主路径：CL:0 + 实时写 socket（与 go2rtc 一致）
                    await asyncio.to_thread(
                        _stream_audio_data_content_length_zero,
                        host=host,
                        port=port,
                        path=path,
                        username=self.username,
                        password=self.password,
                        g711_data=g711_data,
                    )
                    logger.info(
                        "海康对讲实时推流完成 codec=%s bytes=%s push_ms=%s",
                        codec,
                        len(g711_data),
                        int((time.perf_counter() - t0) * 1000),
                    )
                except Exception as e:
                    # 回退：整包 Content-Length（部分固件只吃这种）
                    logger.warning(
                        "海康实时推流失败，回退整包 PUT: %s", e
                    )
                    audio_url = f"{self.base_url}{path}"
                    put_resp = await client.put(
                        audio_url,
                        content=g711_data,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(len(g711_data)),
                        },
                    )
                    put_resp.raise_for_status()
                    await asyncio.sleep(len(g711_data) / float(_G711_RATE) + 0.5)
                finally:
                    try:
                        await client.put(self._url("/close"))
                    except Exception:
                        logger.debug("hikvision close after play ignored", exc_info=True)
                    # 固件需要短暂恢复；否则上一段未完全释放时下一段 open 失败像「不播」
                    await asyncio.sleep(0.35)

    async def play_ulaw(self, ulaw_data: bytes) -> None:
        await self.play_g711(ulaw_data, codec="ulaw")

    async def play_wav(self, wav_data: bytes, *, volume_percent: int = 100) -> str:
        auth = httpx.DigestAuth(self.username, self.password)
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=False) as client:
            codec = await self._detect_codec(client)
        g711 = await asyncio.to_thread(
            wav_bytes_to_g711_8k,
            wav_data,
            codec=codec,
            volume_percent=volume_percent,
        )
        await self.play_g711(g711, codec=codec)
        return codec
