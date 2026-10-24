"""迎宾 TTS：本机扬声器，或按开关推到海康摄像头对讲扬声器。"""
from __future__ import annotations

import asyncio
import logging
import platform
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class TTSProvider:
    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        raise NotImplementedError


class LocalTTS(TTSProvider):
    """本地 TTS - Windows SAPI / Linux espeak"""

    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        try:
            if platform.system() == "Windows":
                import win32com.client  # type: ignore

                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                speaker.Speak(text)
            else:
                import subprocess

                subprocess.run(["espeak", "-v", "zh", text], check=False, timeout=30)
        except Exception as e:
            logger.warning("TTS failed: %s - %s", text[:50], e)

    def synthesize_wav_sync(self, text: str) -> bytes:
        """合成 WAV 字节，供摄像头推流；失败返回空。"""
        try:
            if platform.system() == "Windows":
                return self._sapi_to_wav(text)
            return self._espeak_to_wav(text)
        except Exception as e:
            logger.warning("TTS synthesize failed: %s - %s", text[:50], e)
            return b""

    def _sapi_to_wav(self, text: str) -> bytes:
        import win32com.client  # type: ignore

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            # SSFMCreateForWrite = 3
            stream.Open(path, 3)
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.AudioOutputStream = stream
            speaker.Speak(text)
            stream.Close()
            return Path(path).read_bytes()
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _espeak_to_wav(self, text: str) -> bytes:
        import subprocess

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            subprocess.run(
                ["espeak", "-v", "zh", "-w", path, text],
                check=False,
                timeout=30,
            )
            data = Path(path).read_bytes()
            return data if len(data) > 44 else b""
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


class CameraSpeakerTTS(TTSProvider):
    """
    业务开关 camera_speaker_tts_enabled：
    - 关闭：本机播报（与历史行为一致）
    - 开启且有 camera_id：合成语音 → 海康 ISAPI 对讲推到该摄像头扬声器
    - 推流失败：回退本机播报，避免迎宾无声
    """

    def __init__(self, local: LocalTTS | None = None):
        self.local = local or LocalTTS()

    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        use_camera = False
        if camera_id is not None:
            try:
                use_camera = await self._camera_speaker_enabled(int(camera_id))
            except Exception:
                logger.exception("读取摄像头播报开关失败 camera_id=%s", camera_id)
                use_camera = False

        if use_camera:
            try:
                ok = await self._play_on_camera(text, int(camera_id))
                if ok:
                    return
                logger.warning("摄像头播报未成功，回退本机 TTS camera_id=%s", camera_id)
            except Exception:
                logger.exception("摄像头播报异常，回退本机 TTS camera_id=%s", camera_id)

        await self.local.speak(text, camera_id=camera_id)

    async def _camera_speaker_enabled(self, camera_id: int) -> bool:
        from app.core.database import AsyncSessionLocal
        from app.models import Camera
        from app.services.system_settings_service import SystemSettingsService

        # 仅读 system_settings / 门店覆盖，不依赖 .env，后台改完即时生效
        async with AsyncSessionLocal() as db:
            cam = await db.get(Camera, camera_id)
            store_id = int(cam.store_id) if cam and cam.store_id else None
            return await SystemSettingsService.effective_bool(
                db,
                "camera_speaker_tts_enabled",
                False,
                store_id=store_id,
            )

    async def _play_on_camera(self, text: str, camera_id: int) -> bool:
        from app.core.config import get_settings
        from app.core.database import AsyncSessionLocal
        from app.models import Camera
        from app.services.camera_credentials import build_connect_source, decrypt_password
        from app.services.hikvision_audio import HikvisionTwoWayAudio, resolve_isapi_base, wav_bytes_to_ulaw8k
        from app.services.system_settings_service import SystemSettingsService

        async with AsyncSessionLocal() as db:
            cam = await db.get(Camera, camera_id)
            if not cam:
                logger.warning("摄像头不存在 id=%s", camera_id)
                return False
            source_url = cam.source_url or ""
            username = cam.username
            password_encrypted = cam.password_encrypted
            store_id = int(cam.store_id) if cam.store_id else None
            settings = get_settings()
            volume = await SystemSettingsService.effective_int(
                db,
                "camera_speaker_volume_percent",
                getattr(settings, "camera_speaker_volume_percent", 80),
                store_id=store_id,
            )

        # USB 数字源无法对讲
        if not source_url or source_url.isdigit():
            logger.info("摄像头无网络地址，跳过对讲播报 id=%s", camera_id)
            return False

        base = resolve_isapi_base(source_url)
        if not base:
            logger.warning("无法解析摄像头 ISAPI 地址 id=%s url=%s", camera_id, source_url)
            return False

        user = (username or "admin").strip() or "admin"
        password = ""
        if password_encrypted:
            try:
                password = decrypt_password(password_encrypted)
            except Exception:
                logger.warning("摄像头密码解密失败 id=%s", camera_id)
                return False
        else:
            # 兼容历史：从连接串拆密码（仅用于播报）
            try:
                connected = build_connect_source(source_url, username, None)
                from urllib.parse import unquote, urlparse

                parsed = urlparse(connected)
                if parsed.password:
                    password = unquote(parsed.password)
                if parsed.username:
                    user = unquote(parsed.username)
            except Exception:
                pass
        if not password:
            logger.warning("摄像头无密码，无法 Digest 对讲 id=%s", camera_id)
            return False

        loop = asyncio.get_running_loop()
        wav = await loop.run_in_executor(None, self.local.synthesize_wav_sync, text)
        if not wav:
            return False
        ulaw = await loop.run_in_executor(
            None,
            lambda: wav_bytes_to_ulaw8k(wav, volume_percent=int(volume)),
        )
        client = HikvisionTwoWayAudio(base, user, password)
        await client.play_ulaw(ulaw)
        logger.info(
            "摄像头迎宾播报完成 camera_id=%s bytes=%s volume=%s%%",
            camera_id,
            len(ulaw),
            volume,
        )
        return True


class TTSWorker:
    def __init__(self, provider: TTSProvider | None = None):
        self.provider = provider or CameraSpeakerTTS()
        self._running = False

    async def run(self, queue: asyncio.Queue) -> None:
        self._running = True
        while self._running:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                text = item.get("text") or ""
                if not text:
                    continue
                camera_id = item.get("camera_id")
                try:
                    camera_id = int(camera_id) if camera_id is not None else None
                except (TypeError, ValueError):
                    camera_id = None
                await self.provider.speak(text, camera_id=camera_id)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("TTS worker error: %s", e)

    def stop(self) -> None:
        self._running = False
