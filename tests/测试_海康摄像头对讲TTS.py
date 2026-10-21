"""海康 ISAPI 对讲推流与摄像头播报开关相关单测。"""
from __future__ import annotations

import struct
import wave
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hikvision_audio import (
    HikvisionTwoWayAudio,
    pcm16le_to_ulaw,
    resolve_isapi_base,
    wav_bytes_to_ulaw8k,
)
from app.workers.tts_worker import CameraSpeakerTTS, LocalTTS


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


@pytest.mark.asyncio
async def test_hikvision_play_ulaw_open_put_close():
    ulaw = b"\xff" * 320
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

        async def put(self, url, content=None, headers=None):
            method_path = url.split("/ISAPI", 1)[-1]
            if content is not None:
                # 消费异步迭代器
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

    with patch("app.services.hikvision_audio.httpx.AsyncClient", _Client):
        client = HikvisionTwoWayAudio("http://192.168.1.64", "admin", "pass")
        # 缩短睡眠，避免单测拖长时间
        with patch("app.services.hikvision_audio._CHUNK_SLEEP", 0):
            await client.play_ulaw(ulaw)

    paths = [p for _, p in calls]
    assert any(p.endswith("/close") for p in paths)
    assert any(p.endswith("/open") for p in paths)
    assert any(p.endswith("/audioData") for p in paths)


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
        patch.object(provider, "_play_on_camera", AsyncMock(return_value=True)) as play,
    ):
        await provider.speak("欢迎回来", camera_id=9)

    play.assert_awaited_once_with("欢迎回来", 9)
    local.speak.assert_not_awaited()


@pytest.mark.asyncio
async def test_camera_speaker_tts_fallback_local_on_fail():
    local = MagicMock(spec=LocalTTS)
    local.speak = AsyncMock()
    provider = CameraSpeakerTTS(local=local)

    with (
        patch.object(provider, "_camera_speaker_enabled", AsyncMock(return_value=True)),
        patch.object(provider, "_play_on_camera", AsyncMock(return_value=False)),
    ):
        await provider.speak("欢迎回来", camera_id=9)

    local.speak.assert_awaited_once()
