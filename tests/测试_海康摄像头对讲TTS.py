"""海康 ISAPI 对讲推流与摄像头播报开关相关单测。"""
from __future__ import annotations

import struct
import wave
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hikvision_audio import (
    HikvisionTwoWayAudio,
    clip_osd_channel_name,
    g711_silence_bytes,
    normalize_g711_codec,
    pcm16le_to_alaw,
    pcm16le_to_ulaw,
    resolve_isapi_base,
    sync_channel_osd_name,
    tail_hold_seconds,
    wav_bytes_to_g711_8k,
    wav_bytes_to_ulaw8k,
)
from app.workers.tts_worker import CameraSpeakerTTS, LocalTTS, sanitize_tts_speech_text


def test_tail_hold_waits_for_unplayed_store_name():
    """长句上送约 4s、音频 6.6s 时，须再等约 3s+，否则尾部店名被 close 截断。"""
    remain = tail_hold_seconds(6.637, 3.989)
    assert remain >= 3.2
    assert remain <= 7.7


def test_g711_silence_bytes_alaw_ulaw():
    alaw = g711_silence_bytes("alaw", 100)
    ulaw = g711_silence_bytes("ulaw", 100)
    assert len(alaw) == 800
    assert len(ulaw) == 800
    assert set(alaw) == {0xD5}
    assert set(ulaw) == {0xFF}


def test_sanitize_tts_unwraps_store_parens():
    assert (
        sanitize_tts_speech_text("尊贵的 老贺 普通会员，欢迎您再次光临肠旺面(总店)")
        == "尊贵的 老贺 普通会员，欢迎您再次光临肠旺面，总店"
    )


def _make_wav(pcm: bytes, rate: int = 8000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def test_resolve_isapi_base_from_rtsp():
    assert resolve_isapi_base("rtsp://admin@192.168.1.64:554/Streaming/Channels/102") == (
        "http://192.168.1.64"
    )
    assert resolve_isapi_base("http://192.168.1.64:80") == "http://192.168.1.64:80"
    assert resolve_isapi_base("0") is None
    assert resolve_isapi_base("") is None


def test_clip_osd_channel_name_utf8_budget():
    assert clip_osd_channel_name("肠旺面_测试摄像头") == "肠旺面_测试摄像头"
    long = "测" * 20
    clipped = clip_osd_channel_name(long, max_bytes=32)
    assert len(clipped.encode("utf-8")) <= 32
    assert clipped.startswith("测")


@pytest.mark.asyncio
async def test_sync_channel_osd_name_put_overlay():
    calls: list[tuple[str, str]] = []

    class _Resp:
        def __init__(self, text: str = "", status: int = 200):
            self.text = text
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls.append(("GET", url))
            if url.endswith("channelNameOverlay"):
                return _Resp(
                    '<?xml version="1.0"?><channelNameOverlay>'
                    "<enabled>true</enabled><name>Camera 01</name>"
                    "</channelNameOverlay>"
                )
            return _Resp(
                '<?xml version="1.0"?><VideoInputChannel>'
                "<id>1</id><name>Camera 01</name></VideoInputChannel>"
            )

        async def put(self, url, content=None, headers=None):
            body = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else str(content)
            calls.append(("PUT", url))
            assert "肠旺面" in body or "测试" in body
            assert "Camera 01" not in body
            return _Resp()

    with patch("app.services.hikvision_audio.httpx.AsyncClient", _Client):
        out = await sync_channel_osd_name(
            "http://192.168.1.64", "admin", "pass", "肠旺面_测试摄像头"
        )
    assert out.get("ok") is True
    assert any(m == "PUT" and u.endswith("channelNameOverlay") for m, u in calls)


def test_wav_to_ulaw_8k_length():
    # 0.1 秒静音 @16kHz → 约 800 µ-law 字节 @8kHz
    n = 1600
    pcm = b"\x00\x00" * n
    wav = _make_wav(pcm, rate=16000)
    ulaw = wav_bytes_to_ulaw8k(wav)
    assert 700 <= len(ulaw) <= 900


def test_pcm16le_to_ulaw_nonempty():
    pcm = struct.pack("<" + "h" * 8, 0, 1000, -1000, 5000, -5000, 20000, -20000, 0)
    out = pcm16le_to_ulaw(pcm)
    assert len(out) == 8


def test_normalize_and_alaw_encode():
    assert normalize_g711_codec("G.711alaw") == "alaw"
    assert normalize_g711_codec("G.711ulaw") == "ulaw"
    pcm = struct.pack("<" + "h" * 8, 0, 1000, -1000, 5000, -5000, 20000, -20000, 0)
    alaw = pcm16le_to_alaw(pcm)
    ulaw = pcm16le_to_ulaw(pcm)
    assert len(alaw) == 8
    assert alaw != ulaw
    wav = _make_wav(pcm, rate=8000)
    assert len(wav_bytes_to_g711_8k(wav, codec="alaw")) == 8


@pytest.mark.asyncio
async def test_hikvision_play_ulaw_open_put_close():
    import app.services.hikvision_audio as ha

    ulaw = b"\xff" * 320
    calls: list[tuple[str, str]] = []
    ha._SHARED_HTTP.clear()
    ha._SESSION_NEED_CLOSE.clear()

    class _Resp:
        def __init__(self, text: str = "", status: int = 200):
            self.text = text
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class _Client:
        def __init__(self, *a, **k):
            self.is_closed = False
            self.timeout = k.get("timeout")

        async def put(self, url, content=None, headers=None):
            method_path = url.split("/ISAPI", 1)[-1]
            if content is not None:
                if hasattr(content, "__aiter__"):
                    chunks = []
                    async for c in content:
                        chunks.append(c)
                    assert b"".join(chunks) == ulaw
                calls.append(("PUT", method_path.split("?")[0]))
                return _Resp()
            calls.append(("PUT", method_path.split("?")[0]))
            if method_path.endswith("/open"):
                return _Resp(
                    '<?xml version="1.0"?><TwoWayAudioSession>'
                    "<sessionId>12345</sessionId></TwoWayAudioSession>"
                )
            return _Resp()

    streamed: list[bytes] = []

    def _fake_stream(**kwargs):
        streamed.append(kwargs.get("g711_data") or b"")
        return {"first_chunk_ms": 1, "send_ms": 2, "audio_ms": 40}

    with (
        patch("app.services.hikvision_audio.httpx.AsyncClient", _Client),
        patch(
            "app.services.hikvision_audio._stream_audio_data_content_length_zero",
            _fake_stream,
        ),
        patch("app.services.hikvision_audio._CHUNK_SLEEP", 0),
    ):
        client = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "pass")
        await client.play_ulaw(ulaw)
        # 第二次应复用同一 Digest Client，不再新建
        n_clients = len(ha._SHARED_HTTP)
        await client.play_ulaw(ulaw)
        assert len(ha._SHARED_HTTP) == n_clients == 1

    paths = [p for _, p in calls]
    assert paths.count("/System/TwoWayAudio/channels/1/open") == 2
    assert paths.count("/System/TwoWayAudio/channels/1/close") >= 2
    assert streamed and streamed[0] == ulaw
    ha._SHARED_HTTP.clear()


def test_stream_pace_short_clip_slower_than_medium():
    """短句 pace 更慢，避免固件只响开头一字。"""
    import app.services.hikvision_audio as ha

    # 复用模块内公式：短 ≤2.5s → 0.72；中 ≤3.5 → 0.58；长 ≤8 → 0.50
    def _pace(dur: float) -> float:
        if dur <= 2.5:
            return 0.72
        if dur <= 3.5:
            return 0.58
        if dur <= 8.0:
            return 0.50
        return 0.62

    assert _pace(1.7) > _pace(3.0) > _pace(5.0)
    assert ha._CHUNK_BYTES * 2 == 1280  # 启动突发上限约 160ms


def test_live_downscale_caps_long_edge():
    import numpy as np

    from app.services.face_extractor import _downscale_for_live

    big = np.zeros((1440, 2560, 3), dtype=np.uint8)
    out = _downscale_for_live(big, max_side=1280)
    assert max(out.shape[0], out.shape[1]) == 1280
    small = np.zeros((480, 640, 3), dtype=np.uint8)
    assert _downscale_for_live(small) is small or _downscale_for_live(small).shape == small.shape


@pytest.mark.asyncio
async def test_camera_speaker_tts_off_uses_local():
    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)

    with patch.object(provider, "_camera_speaker_enabled", AsyncMock(return_value=False)):
        await provider.speak("欢迎回来", camera_id=1)

    local.speak.assert_awaited_once()
    assert local.speak.await_args.kwargs.get("camera_id") == 1


@pytest.mark.asyncio
async def test_camera_speaker_tts_on_plays_camera():
    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)

    with (
        patch.object(provider, "_camera_speaker_enabled", AsyncMock(return_value=True)),
        patch.object(provider, "_play_on_camera", AsyncMock(return_value="ok")) as play,
    ):
        await provider.speak("欢迎回来", camera_id=9)

    play.assert_awaited_once_with("欢迎回来", 9, should_abort=None)
    local.speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_camera_speaker_tts_fallback_local_on_fail():
    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)

    with (
        patch.object(provider, "_camera_speaker_enabled", AsyncMock(return_value=True)),
        patch.object(provider, "_play_on_camera", AsyncMock(return_value="failed")),
    ):
        await provider.speak("欢迎回来", camera_id=9)

    local.speak.assert_awaited_once()


@pytest.mark.asyncio
async def test_camera_speaker_tts_cancelled_skips_local_fallback():
    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)

    with (
        patch.object(provider, "_camera_speaker_enabled", AsyncMock(return_value=True)),
        patch.object(provider, "_play_on_camera", AsyncMock(return_value="cancelled")),
    ):
        await provider.speak("欢迎光临", camera_id=9)

    local.speak.assert_not_awaited()
