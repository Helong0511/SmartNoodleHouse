"""迎宾 TTS：本机扬声器，或按开关推到海康摄像头对讲扬声器。"""
from __future__ import annotations

import asyncio
import logging
import platform
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class TTSProvider:
    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        raise NotImplementedError


def _ps_escape(text: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return (text or "").replace("'", "''")


def _ensure_pywin32() -> bool:
    """uvicorn --reload 子进程常缺 pywintypes DLL 搜索路径。"""
    try:
        import pywintypes  # noqa: F401

        return True
    except ImportError:
        pass
    import os
    import sys
    from pathlib import Path

    base = Path(sys.prefix) / "Lib" / "site-packages"
    for sub in ("pywin32_system32", "win32", "win32/lib", "Pythonwin"):
        p = base / sub
        if not p.is_dir():
            continue
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
        try:
            os.add_dll_directory(s)
        except Exception:
            pass
        path_env = os.environ.get("PATH", "")
        if s not in path_env:
            os.environ["PATH"] = s + os.pathsep + path_env
    try:
        import pywintypes  # noqa: F401

        return True
    except ImportError:
        return False


class LocalTTS(TTSProvider):
    """本地 TTS：Windows 优先 System.Speech，其次 pywin32/SAPI；Linux 用 espeak。"""

    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if platform.system() == "Windows":
            # System.Speech 优先：不依赖 pywintypes，reload 子进程更稳
            if self._speak_system_speech(text):
                return
            if self._speak_win32com(text):
                return
            logger.warning("TTS failed: Windows SAPI 均不可用 text=%s", text[:50])
            return
        try:
            subprocess.run(["espeak", "-v", "zh", text], check=False, timeout=30)
        except Exception as e:
            logger.warning("TTS failed: %s - %s", text[:50], e)

    def _speak_win32com(self, text: str) -> bool:
        try:
            if not _ensure_pywin32():
                raise ImportError("pywintypes")
            import win32com.client  # type: ignore

            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Speak(text)
            return True
        except Exception as e:
            logger.warning("win32com TTS 不可用，尝试 System.Speech: %s", e)
            return False

    def _speak_system_speech(self, text: str) -> bool:
        """不依赖 pywin32：用 PowerShell + System.Speech 本机播报。"""
        try:
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.SetOutputToDefaultAudioDevice(); "
                "$s.Volume = 100; "
                f"$s.Speak('{_ps_escape(text)}'); "
                "$s.Dispose()"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                check=False,
                timeout=60,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                logger.warning(
                    "System.Speech Speak 失败 code=%s err=%s",
                    r.returncode,
                    (r.stderr or "")[:200],
                )
                return False
            return True
        except Exception as e:
            logger.warning("System.Speech Speak 异常: %s", e)
            return False

    def synthesize_wav_sync(self, text: str) -> bytes:
        """合成 WAV 字节，供摄像头推流；失败返回空。"""
        text = (text or "").strip()
        if not text:
            return b""
        try:
            if platform.system() == "Windows":
                # System.Speech 优先，避免 uvicorn --reload 子进程缺 pywintypes
                wav = self._sapi_to_wav_system_speech(text)
                if wav:
                    return wav
                return self._sapi_to_wav_win32com(text)
            return self._espeak_to_wav(text)
        except Exception as e:
            logger.warning("TTS synthesize failed: %s - %s", text[:50], e)
            return b""

    def _sapi_to_wav_win32com(self, text: str) -> bytes:
        try:
            if not _ensure_pywin32():
                raise ImportError("pywintypes")
            import win32com.client  # type: ignore
        except Exception as e:
            logger.warning("win32com 合成不可用: %s", e)
            return b""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(path, 3)  # SSFMCreateForWrite
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.AudioOutputStream = stream
            speaker.Speak(text)
            stream.Close()
            data = Path(path).read_bytes()
            return data if len(data) > 44 else b""
        except Exception as e:
            logger.warning("win32com 写 WAV 失败: %s", e)
            return b""
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _sapi_to_wav_system_speech(self, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            # 路径用单引号；反斜杠在 PowerShell 单引号中字面量即可
            out = path.replace("'", "''")
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{out}'); "
                f"$s.Speak('{_ps_escape(text)}'); "
                "$s.Dispose()"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                check=False,
                timeout=60,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                logger.warning(
                    "System.Speech WAV 失败 code=%s err=%s",
                    r.returncode,
                    (r.stderr or "")[:200],
                )
                return b""
            data = Path(path).read_bytes()
            return data if len(data) > 44 else b""
        except Exception as e:
            logger.warning("System.Speech WAV 异常: %s", e)
            return b""
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _espeak_to_wav(self, text: str) -> bytes:
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

    async def speak(
        self,
        text: str,
        *,
        camera_id: int | None = None,
        also_local: bool = False,
        force_local: bool = False,
        force_camera: bool = False,
    ) -> None:
        """播报路由。

        - force_local：只走电脑扬声器（试听电脑）
        - force_camera：只走摄像头对讲，失败不回退本机（试听摄像头）
        - also_local：摄像头成功后仍再播本机（旧 both 试听）
        """
        if force_local:
            await self.local.speak(text, camera_id=camera_id)
            return

        use_camera = False
        if camera_id is not None:
            if force_camera:
                use_camera = True
            else:
                try:
                    use_camera = await self._camera_speaker_enabled(int(camera_id))
                except Exception:
                    logger.exception("读取摄像头播报开关失败 camera_id=%s", camera_id)
                    use_camera = False

        camera_ok = False
        if use_camera:
            try:
                camera_ok = await self._play_on_camera(text, int(camera_id))
                if not camera_ok:
                    logger.warning("摄像头播报未成功 camera_id=%s", camera_id)
            except Exception:
                logger.exception("摄像头播报异常 camera_id=%s", camera_id)
                camera_ok = False

        if force_camera:
            if not camera_ok:
                raise RuntimeError("摄像头播报失败（对讲未成功）")
            return

        # 正常迎宾：摄像头失败才回退本机；also_local 时叠播本机
        if also_local or not camera_ok:
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
            # 对讲走 HTTP ISAPI，与 RTSP 拉流是否 ONLINE 无关；勿因 RECONNECTING 直接跳过
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

        import time

        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        wav = await loop.run_in_executor(None, self.local.synthesize_wav_sync, text)
        synth_ms = int((time.perf_counter() - t0) * 1000)
        if not wav:
            logger.warning("TTS 合成 WAV 为空，无法推摄像头 camera_id=%s", camera_id)
            return False
        t1 = time.perf_counter()
        ulaw = await loop.run_in_executor(
            None,
            lambda: wav_bytes_to_ulaw8k(wav, volume_percent=int(volume)),
        )
        encode_ms = int((time.perf_counter() - t1) * 1000)
        # 连接 5s；整段推流超时在 play_ulaw 内按音频时长计算
        client = HikvisionTwoWayAudio(base, user, password, timeout=30.0)
        t2 = time.perf_counter()
        await client.play_ulaw(ulaw)
        push_ms = int((time.perf_counter() - t2) * 1000)
        audio_ms = int(len(ulaw) / 8.0)  # 8kHz µ-law
        logger.info(
            "摄像头迎宾播报完成 camera_id=%s bytes=%s volume=%s%% "
            "synth_ms=%s encode_ms=%s push_ms=%s audio_ms=%s total_ms=%s",
            camera_id,
            len(ulaw),
            volume,
            synth_ms,
            encode_ms,
            push_ms,
            audio_ms,
            synth_ms + encode_ms + push_ms,
        )
        return True


class TTSWorker:
    def __init__(self, provider: TTSProvider | None = None):
        self.provider = provider or CameraSpeakerTTS()
        self._running = False

    async def run(self, queue: asyncio.Queue | None = None) -> None:
        """消费迎宾队列。

        默认走 WelcomeService.dequeue（Redis），热重载后仍能收到播报。
        测试可传入固定 asyncio.Queue 覆盖。
        """
        self._running = True
        if platform.system() == "Windows":
            _ensure_pywin32()
        while self._running:
            try:
                if queue is not None:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                else:
                    from app.services.face_service import WelcomeService

                    item = await WelcomeService.dequeue(timeout=1.0)
                    if item is None:
                        continue
                text = item.get("text") or ""
                if not text:
                    continue
                camera_id = item.get("camera_id")
                kind = item.get("kind") or "member"
                try:
                    camera_id = int(camera_id) if camera_id is not None else None
                except (TypeError, ValueError):
                    camera_id = None
                logger.info(
                    "TTS 开始播报 kind=%s camera_id=%s text=%s",
                    kind,
                    camera_id,
                    text[:40],
                )
                await self.provider.speak(
                    text,
                    camera_id=camera_id,
                    force_local=(kind == "test_speak_pc"),
                    force_camera=(kind == "test_speak_camera"),
                    also_local=(kind == "test_speak"),
                )
                logger.info("TTS 播报结束 kind=%s camera_id=%s", kind, camera_id)
            except Exception as e:
                logger.error("TTS worker error: %s", e)

    def stop(self) -> None:
        self._running = False
