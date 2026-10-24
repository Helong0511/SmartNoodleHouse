"""海康 ISAPI 双向对讲：把 G.711 µ-law 音频推到摄像头扬声器。"""
from __future__ import annotations

import asyncio
import logging
import struct
import wave
from io import BytesIO
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# G.711 µ-law 实时推流：8kHz 单声道 = 8000 字节/秒
_ULAW_RATE = 8000
_CHUNK_BYTES = 160  # 20ms
_CHUNK_SLEEP = _CHUNK_BYTES / _ULAW_RATE
# 同摄像头对讲会话互斥（跨 TTSWorker 实例）
_CAMERA_LOCKS: dict[str, asyncio.Lock] = {}
_CAMERA_LOCKS_GUARD = asyncio.Lock()

# ITU-T G.711 µ-law（与 CPython audioop.lin2ulaw 同思路）
_BIAS = 0x84
_CLIP = 32635
_EXP_LUT = (
    [0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3]
    + [4] * 16
    + [5] * 32
    + [6] * 64
    + [7] * 128
)


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
    # RTSP/RTSPS：管理口默认 HTTP 80
    return f"http://{host}"


def linear16_to_ulaw(sample: int) -> int:
    """16-bit PCM → µ-law 字节。"""
    sample = max(-32768, min(32767, int(sample)))
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    if sample > _CLIP:
        sample = _CLIP
    sample = sample + _BIAS
    exponent = _EXP_LUT[(sample >> 7) & 0xFF]
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def pcm16le_to_ulaw(pcm: bytes) -> bytes:
    out = bytearray(len(pcm) // 2)
    for i in range(0, len(pcm) - 1, 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        out[i // 2] = linear16_to_ulaw(sample)
    return bytes(out)


def resample_pcm16_mono(pcm: bytes, src_rate: int, dst_rate: int = _ULAW_RATE) -> bytes:
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
    """按百分比缩放 16-bit PCM 音量（0=静音，100=原音，可略超 100 但会限幅）。"""
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


def wav_bytes_to_ulaw8k(wav_data: bytes, *, volume_percent: int = 100) -> bytes:
    """WAV（任意常见 PCM）→ 8kHz mono G.711 µ-law 裸流；可选音量百分比。"""
    with wave.open(BytesIO(wav_data), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise ValueError(f"仅支持 16-bit PCM WAV，当前 sampwidth={sampwidth}")
    if channels > 1:
        # 取左声道
        mono = bytearray()
        for i in range(0, len(frames), sampwidth * channels):
            mono.extend(frames[i : i + 2])
        frames = bytes(mono)
    pcm8k = resample_pcm16_mono(frames, rate, _ULAW_RATE)
    pcm8k = scale_pcm16_volume(pcm8k, volume_percent)
    return pcm16le_to_ulaw(pcm8k)


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


class HikvisionTwoWayAudio:
    """对一台海康摄像头打开对讲通道并推送 µ-law 音频。"""

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

    def _url(self, suffix: str) -> str:
        return f"{self.base_url}/ISAPI/System/TwoWayAudio/channels/{self.channel}{suffix}"

    async def play_ulaw(self, ulaw_data: bytes) -> None:
        if not ulaw_data:
            return
        auth = httpx.DigestAuth(self.username, self.password)
        # µ-law 8kHz 实时推流：读超时需覆盖整段音频，否则会 ReadTimeout 后无声
        stream_secs = max(float(self.timeout), len(ulaw_data) / 8000.0 + 15.0)
        timeout = httpx.Timeout(stream_secs, connect=5.0, write=stream_secs, pool=10.0)
        lock = await self._lock_for()
        async with lock:
            async with httpx.AsyncClient(auth=auth, timeout=timeout, follow_redirects=False) as client:
                # 清理残留会话（忽略失败）
                try:
                    await client.put(self._url("/close"))
                except Exception:
                    logger.debug("hikvision close stale session ignored", exc_info=True)

                open_resp = await client.put(self._url("/open"))
                open_resp.raise_for_status()
                session_id = _parse_session_id(open_resp.text) or ""
                audio_url = self._url("/audioData")
                if session_id:
                    audio_url = f"{audio_url}?sessionId={session_id}"

                play_secs = len(ulaw_data) / float(_ULAW_RATE)
                try:
                    # 部分海康固件对 chunked 流式 PUT 会中途断开；短句整包更稳
                    # 长音频仍按实时块推，避免一次性撑满内存/缓冲
                    if len(ulaw_data) <= 8000 * 8:  # ≤约 8 秒
                        put_resp = await client.put(
                            audio_url,
                            content=ulaw_data,
                            headers={
                                "Content-Type": "application/octet-stream",
                                "Content-Length": str(len(ulaw_data)),
                            },
                        )
                        put_resp.raise_for_status()
                        # 整包 PUT 常秒回 200；立刻 close 会打断播放 → 有时有声有时无声
                        await asyncio.sleep(play_secs + 0.35)
                    else:

                        async def _body():
                            for i in range(0, len(ulaw_data), _CHUNK_BYTES):
                                chunk = ulaw_data[i : i + _CHUNK_BYTES]
                                yield chunk
                                await asyncio.sleep(_CHUNK_SLEEP)

                        put_resp = await client.put(
                            audio_url,
                            content=_body(),
                            headers={"Content-Type": "application/octet-stream"},
                        )
                        put_resp.raise_for_status()
                finally:
                    try:
                        await client.put(self._url("/close"))
                    except Exception:
                        logger.debug("hikvision close after play ignored", exc_info=True)

    async def play_wav(self, wav_data: bytes) -> None:
        await self.play_ulaw(wav_bytes_to_ulaw8k(wav_data))
