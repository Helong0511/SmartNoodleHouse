"""TTS_VOICE / TTS_PROVIDER 解析与 edge 合成路径（mock，不访问外网）。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.workers.tts_worker import (
    LocalTTS,
    resolve_tts_provider,
    resolve_tts_rate,
    resolve_tts_voice,
    sanitize_tts_speech_text,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("gentle_female", "zh-CN-XiaoxiaoNeural"),
        ("温柔女生", "zh-CN-XiaoxiaoNeural"),
        ("女生", "zh-CN-XiaoxiaoNeural"),
        ("female", "zh-CN-XiaoxiaoNeural"),
        ("zh-CN", "zh-CN-XiaoxiaoNeural"),
        ("男生", "zh-CN-YunjianNeural"),
        ("male", "zh-CN-YunjianNeural"),
        ("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoxiaoNeural"),
        ("", "zh-CN-XiaoxiaoNeural"),
    ],
)
def test_resolve_tts_voice_aliases(raw, expected):
    assert resolve_tts_voice(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("edge", "edge"),
        ("edge-tts", "edge"),
        ("neural", "edge"),
        ("local", "local"),
        ("LOCAL", "local"),
        ("", "edge"),
    ],
)
def test_resolve_tts_provider(raw, expected):
    assert resolve_tts_provider(raw) == expected


def test_resolve_tts_rate_default():
    assert resolve_tts_rate("") == "-10%"
    assert resolve_tts_rate("+0%") == "+0%"


def test_sanitize_still_unwraps_parens():
    assert "总店" in sanitize_tts_speech_text("欢迎光临肠旺面(总店)")


def test_edge_tts_to_wav_uses_voice_and_ffmpeg(tmp_path: Path):
    """合成走 edge-tts + ffmpeg；不真连微软。"""
    wav_bytes = b"RIFF" + b"\x00" * 44 + b"pcm"

    class _FakeCommunicate:
        def __init__(self, text, voice, rate="-10%"):
            self.text = text
            self.voice = voice
            self.rate = rate

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"fake-mp3")

    fake_edge = MagicMock()
    fake_edge.Communicate = _FakeCommunicate

    def fake_run(cmd, **kwargs):
        # ffmpeg ... wav_path
        out = Path(cmd[-1])
        out.write_bytes(wav_bytes)
        return MagicMock(returncode=0, stderr=b"")

    tts = LocalTTS()
    with (
        patch("app.workers.tts_worker.resolve_tts_provider", return_value="edge"),
        patch("app.workers.tts_worker.resolve_tts_voice", return_value="zh-CN-XiaoxiaoNeural"),
        patch("app.workers.tts_worker.resolve_tts_rate", return_value="-10%"),
        patch.dict("sys.modules", {"edge_tts": fake_edge}),
        patch("app.workers.tts_worker.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("app.workers.tts_worker.subprocess.run", side_effect=fake_run),
    ):
        data = tts._edge_tts_to_wav("欢迎光临")
    assert data == wav_bytes


def test_synthesize_wav_falls_back_when_edge_disabled():
    tts = LocalTTS()
    with (
        patch("app.workers.tts_worker.resolve_tts_provider", return_value="local"),
        patch.object(tts, "_edge_tts_to_wav", return_value=b"") as edge,
        patch.object(tts, "_sapi_to_wav_win32com", return_value=b""),
        patch.object(tts, "_sapi_to_wav_system_speech", return_value=b""),
        patch.object(tts, "_espeak_to_wav", return_value=b"RIFF" + b"\x00" * 50),
        patch("app.workers.tts_worker.platform.system", return_value="Linux"),
    ):
        data = tts.synthesize_wav_sync("试听")
    edge.assert_not_called()
    assert data.startswith(b"RIFF")
