from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.hot_cache import HotCache
from app.services import hikvision_audio as audio


def test_clip_osd_channel_name_preserves_utf8_boundaries() -> None:
    name = "射洪肠旺面欢迎光临今日新品推荐"
    clipped = audio.clip_osd_channel_name(name, max_bytes=17)
    assert clipped.encode("utf-8").decode("utf-8") == clipped
    assert len(clipped.encode("utf-8")) <= 17
    assert name.startswith(clipped)


def test_xml_set_tag_text_escapes_untrusted_text() -> None:
    result = audio._xml_set_tag_text(
        "<Channel><name>old</name><enabled>true</enabled></Channel>",
        "name",
        'A&B<"shop">',
    )
    assert "<name>A&amp;B&lt;&quot;shop&quot;&gt;</name>" in result
    assert "<enabled>true</enabled>" in result


def test_xml_set_tag_text_rejects_missing_tag() -> None:
    with pytest.raises(ValueError, match="缺少"):
        audio._xml_set_tag_text("<Channel />", "name", "shop")


def test_parse_session_id_supports_namespaces() -> None:
    xml = '<Response xmlns="urn:hikvision"><sessionId>  session-123  </sessionId></Response>'
    assert audio._parse_session_id(xml) == "session-123"
    assert audio._parse_session_id("<broken") is None


def test_parse_channel_audio_reads_codec_and_enabled_state() -> None:
    xml = """
    <TwoWayAudioChannelList>
      <TwoWayAudioChannel>
        <id>2</id>
        <audioCompressionType>G.711 A-law</audioCompressionType>
        <enabled>false</enabled>
      </TwoWayAudioChannel>
    </TwoWayAudioChannelList>
    """
    assert audio._parse_channel_audio(xml, 2) == ("alaw", False)
    assert audio._parse_channel_audio(xml, 1) == ("ulaw", True)


def test_g711_silence_has_correct_codec_fill_and_duration() -> None:
    assert audio.g711_silence_bytes("alaw", 100) == bytes([0xD5]) * 800
    assert audio.g711_silence_bytes("ulaw", 100) == bytes([0xFF]) * 800
    assert audio.g711_silence_bytes("ulaw", 0) == b""


def test_speaker_volume_xml_is_clamped_and_patched() -> None:
    xml = "<TwoWayAudioChannel><speakerVolume>30</speakerVolume></TwoWayAudioChannel>"
    assert audio.patch_twoway_speaker_volume_xml(xml, 130) == (
        "<TwoWayAudioChannel><speakerVolume>100</speakerVolume></TwoWayAudioChannel>"
    )
    assert audio.patch_twoway_speaker_volume_xml("<x />", 50) is None


def test_digest_authorization_uses_incrementing_nonce_count() -> None:
    challenge = (
        'Digest realm="Hikvision", nonce="nonce-1", qop="auth", '
        'opaque="opaque-1", algorithm=MD5'
    )
    first = audio._build_digest_authorization(
        challenge,
        method="PUT",
        uri="/ISAPI/System/TwoWayAudio/channels/1/audioData",
        username="admin",
        password="secret",
        nonce_count=1,
    )
    second = audio._build_digest_authorization(
        challenge,
        method="PUT",
        uri="/ISAPI/System/TwoWayAudio/channels/1/audioData",
        username="admin",
        password="secret",
        nonce_count=2,
    )
    assert "nc=00000001" in first
    assert "nc=00000002" in second
    assert first != second


def test_digest_authorization_rejects_header_injection() -> None:
    challenge = 'Digest realm="Hikvision", nonce="nonce-1", qop="auth"'
    kwargs = {
        "method": "PUT",
        "uri": "/ISAPI/System/TwoWayAudio/channels/1/audioData",
        "password": "secret",
    }
    with pytest.raises(ValueError, match="username"):
        audio._build_digest_authorization(
            challenge,
            username="admin\r\nX-Injected: yes",
            **kwargs,
        )
    with pytest.raises(ValueError, match="nonce"):
        audio._build_digest_authorization(
            'Digest realm="Hikvision", nonce="nonce-1\r\nX: y", qop="auth"',
            username="admin",
            **kwargs,
        )


def test_audio_transport_requires_pinned_ip_and_safe_path(monkeypatch) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("validation must happen before opening a socket")

    monkeypatch.setattr(audio.socket, "create_connection", unexpected_connect)
    with pytest.raises(ValueError, match="pinned IP"):
        audio._stream_audio_data_content_length_zero(
            host="camera.example.com",
            port=80,
            path="/ISAPI/System/TwoWayAudio/channels/1/audioData",
            username="admin",
            password="secret",
            g711_data=b"\x7f" * 640,
        )
    with pytest.raises(ValueError, match="request path"):
        audio._stream_audio_data_content_length_zero(
            host="10.0.0.10",
            port=80,
            path="/ISAPI/System/TwoWayAudio/channels/1/audioData\r\nX-Injected: yes",
            username="admin",
            password="secret",
            g711_data=b"\x7f" * 640,
        )


def test_audio_transport_increments_digest_nonce_count_across_streams(monkeypatch) -> None:
    challenge = (
        'HTTP/1.1 401 Unauthorized\r\n'
        'WWW-Authenticate: Digest realm="Hikvision", nonce="nonce-1", '
        'qop="auth", opaque="opaque-1", algorithm=MD5\r\n'
        'Content-Length: 0\r\n\r\n'
    ).encode("latin-1")
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    class FakeSocket:
        def __init__(self, response: bytes) -> None:
            self.response = response
            self.sent: list[bytes] = []
            self.closed = False

        def settimeout(self, _timeout: float) -> None:
            return None

        def sendall(self, payload: bytes) -> None:
            self.sent.append(payload)

        def recv(self, _size: int) -> bytes:
            response, self.response = self.response, b""
            return response

        def shutdown(self, _how: int) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    all_sockets = [
        FakeSocket(challenge),
        FakeSocket(ok),
        FakeSocket(ok),
    ]
    socket_queue = list(all_sockets)
    monkeypatch.setattr(audio.socket, "create_connection", lambda *_a, **_k: socket_queue.pop(0))
    monkeypatch.setattr(
        audio,
        "_DIGEST_WWW_CACHE",
        HotCache("test.hikvision.digest_www", max_size=32, ttl_seconds=120.0),
    )
    monkeypatch.setattr(
        audio,
        "_DIGEST_NONCE_CACHE",
        HotCache("test.hikvision.digest_nonce", max_size=32, ttl_seconds=120.0),
    )

    args = {
        "host": "10.0.0.10",
        "port": 80,
        "path": "/ISAPI/System/TwoWayAudio/channels/1/audioData",
        "username": "admin",
        "password": "secret",
        "g711_data": b"\x7f" * 640,
    }
    audio._stream_audio_data_content_length_zero(**args)
    first_authorized_header = b"".join(all_sockets[1].sent[:1]).decode("latin-1")
    audio._stream_audio_data_content_length_zero(**args)
    second_authorized_header = b"".join(all_sockets[2].sent[:1]).decode("latin-1")

    assert "nc=00000001" in first_authorized_header
    assert "nc=00000002" in second_authorized_header
    assert b"".join(all_sockets[1].sent[1:]) == args["g711_data"]
    assert b"".join(all_sockets[2].sent[1:]) == args["g711_data"]


def test_hikvision_audio_constructor_pins_and_validates_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.camera_credentials.pin_camera_url_for_connect",
        lambda _url: "http://10.0.0.10:80",
    )
    client = audio.HikvisionTwoWayAudio("http://camera.local", "admin", "secret")
    assert client.base_url == "http://10.0.0.10:80"

    with pytest.raises(ValueError, match="base URL"):
        audio.HikvisionTwoWayAudio("http://admin:secret@10.0.0.10", "admin", "secret")
    with pytest.raises(ValueError, match="base URL"):
        audio.HikvisionTwoWayAudio("http://10.0.0.10/ISAPI", "admin", "secret")
    with pytest.raises(ValueError, match="username"):
        audio.HikvisionTwoWayAudio("http://10.0.0.10", "admin\r\nX: y", "secret")


def test_play_g711_rejects_payload_over_two_minutes(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.camera_credentials.pin_camera_url_for_connect",
        lambda _url: "http://10.0.0.10:80",
    )
    client = audio.HikvisionTwoWayAudio("http://10.0.0.10", "admin", "secret")
    result = asyncio.run(client.play_g711(b"\x7f" * (audio._MAX_G711_PAYLOAD_BYTES + 1)))
    assert result["status"] == "failed"
    assert result["error"] == "payload_too_large"


def test_try_sync_osd_treats_unsafe_target_as_best_effort(monkeypatch) -> None:
    def reject_target(_source_url: str):
        raise ValueError("unsafe camera URL")

    monkeypatch.setattr(audio, "resolve_isapi_base", reject_target)
    result = asyncio.run(
        audio.try_sync_camera_osd_name(
            source_url="http://public.example.com",
            username="admin",
            password_encrypted=None,
            display_name="射洪肠旺面",
        )
    )
    assert result == {"ok": False, "reason": "unsafe_source"}


def test_hikvision_async_state_is_isolated_per_event_loop(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.camera_credentials.pin_camera_url_for_connect",
        lambda _url: "http://10.0.0.10:80",
    )

    async def get_state_and_lock():
        client = audio.HikvisionTwoWayAudio("http://10.0.0.10", "admin", "secret")
        return audio._get_loop_state(), await client._lock_for()

    first_state, first_lock = asyncio.run(get_state_and_lock())
    second_state, second_lock = asyncio.run(get_state_and_lock())

    assert first_state is not second_state
    assert first_lock is not second_lock
