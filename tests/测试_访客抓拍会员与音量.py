"""访客抓拍：会员落库、同人访客号、摄像头音量增益。"""
from __future__ import annotations

import struct
import wave
from io import BytesIO

from app.services.hikvision_audio import (
    patch_twoway_speaker_volume_xml,
    scale_pcm16_volume,
    wav_bytes_to_ulaw8k,
)
from app.services.system_settings_service import BUSINESS_SETTING_META, SystemSettingsService


def test_camera_speaker_volume_meta():
    assert SystemSettingsService.allows_store_override("camera_speaker_volume_percent") is True
    meta = BUSINESS_SETTING_META["camera_speaker_volume_percent"]
    assert meta["group"] == "welcome"
    assert meta["type"] == "int"
    assert meta.get("min") == 0
    assert meta.get("max") == 100
    assert SystemSettingsService._validate_value("camera_speaker_volume_percent", "50") == "50"
    assert "speakerVolume" in (meta.get("description") or "")


def test_scale_pcm16_volume_half_and_mute():
    # 构造 4 个样本
    pcm = struct.pack("<4h", 10000, -10000, 2000, -2000)
    half = scale_pcm16_volume(pcm, 50)
    vals = struct.unpack("<4h", half)
    assert vals == (5000, -5000, 1000, -1000)
    mute = scale_pcm16_volume(pcm, 0)
    assert struct.unpack("<4h", mute) == (0, 0, 0, 0)
    same = scale_pcm16_volume(pcm, 100)
    assert same == pcm


def test_patch_twoway_speaker_volume_xml():
    xml = (
        '<?xml version="1.0"?>'
        "<TwoWayAudioChannel>"
        "<id>1</id>"
        "<speakerVolume>50</speakerVolume>"
        "<microphoneVolume>100</microphoneVolume>"
        "</TwoWayAudioChannel>"
    )
    out = patch_twoway_speaker_volume_xml(xml, 20)
    assert out is not None
    assert "<speakerVolume>20</speakerVolume>" in out
    assert "<microphoneVolume>100</microphoneVolume>" in out
    assert patch_twoway_speaker_volume_xml("<TwoWayAudioChannel/>", 30) is None
    assert patch_twoway_speaker_volume_xml(xml, 150) is not None
    assert "<speakerVolume>100</speakerVolume>" in patch_twoway_speaker_volume_xml(xml, 150)


def _make_wav_pcm16(samples: list[int], rate: int = 8000) -> bytes:
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def test_wav_to_ulaw_respects_volume():
    loud = [8000] * 160
    wav = _make_wav_pcm16(loud)
    full = wav_bytes_to_ulaw8k(wav, volume_percent=100)
    quiet = wav_bytes_to_ulaw8k(wav, volume_percent=10)
    assert len(full) == len(quiet) == 160
    # µ-law 编码后静音/小音量字节分布应不同
    assert full != quiet
