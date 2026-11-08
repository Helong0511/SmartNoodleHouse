"""迎宾 TTS：本机扬声器，或按开关推到海康摄像头对讲扬声器。"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 友好别名 → Microsoft Edge 神经音色（Ubuntu 生产默认温柔女声）
_TTS_VOICE_ALIASES: dict[str, str] = {
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "female": "zh-CN-XiaoxiaoNeural",
    "女生": "zh-CN-XiaoxiaoNeural",
    "gentle_female": "zh-CN-XiaoxiaoNeural",
    "温柔女生": "zh-CN-XiaoxiaoNeural",
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "male": "zh-CN-YunjianNeural",
    "男生": "zh-CN-YunjianNeural",
    "yunjian": "zh-CN-YunjianNeural",
}

# Windows SAPI：按别名优先匹配的描述关键字（小写）
_SAPI_VOICE_HINTS: dict[str, tuple[str, ...]] = {
    "female": ("huihui", "yaoyao", "female", "zira", "xiaoxiao"),
    "gentle_female": ("huihui", "yaoyao", "female", "xiaoxiao"),
    "温柔女生": ("huihui", "yaoyao", "female"),
    "女生": ("huihui", "yaoyao", "female"),
    "male": ("kangkang", "male", "david", "mark", "yunjian"),
    "男生": ("kangkang", "male"),
}


def sanitize_tts_speech_text(text: str) -> str:
    """合成前润色：SAPI 常把括号当旁白弱读/跳过，店名「肠旺面(总店)」易丢尾。"""
    s = (text or "").strip()
    if not s:
        return ""
    s = (
        s.replace("（", "，")
        .replace("）", "")
        .replace("(", "，")
        .replace(")", "")
    )
    s = re.sub(r"[，,]{2,}", "，", s)
    return s.strip("，, ").strip()


def resolve_tts_voice(raw: str | None = None) -> str:
    """解析 TTS_VOICE：别名映射到 Edge 神经音色；已是正式名则原样返回。"""
    if raw is None:
        try:
            from app.core.config import get_settings

            raw = get_settings().tts_voice
        except Exception:
            raw = "gentle_female"
    key = (raw or "").strip()
    if not key:
        key = "gentle_female"
    mapped = _TTS_VOICE_ALIASES.get(key.lower()) or _TTS_VOICE_ALIASES.get(key)
    return mapped or key


def resolve_tts_provider(raw: str | None = None) -> str:
    """edge=微软神经 TTS（需出网+ffmpeg）；local=本机 SAPI/espeak。"""
    if raw is None:
        try:
            from app.core.config import get_settings

            raw = get_settings().tts_provider
        except Exception:
            raw = "edge"
    p = (raw or "edge").strip().lower()
    if p in ("edge", "edge-tts", "neural"):
        return "edge"
    return "local"


def resolve_tts_rate(raw: str | None = None) -> str:
    """语速：略慢更接近「温柔」；空则默认 -10%。"""
    if raw is None:
        try:
            from app.core.config import get_settings

            raw = getattr(get_settings(), "tts_rate", None)
        except Exception:
            raw = None
    s = (raw or "").strip()
    return s or "-10%"


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


# 短句 WAV / G.711：统一 HotCache（启动预热后试听/迎宾可跳过合成与转码）
from app.core.hot_cache import HotCache

_WAV_CACHE: HotCache[str, bytes] = HotCache("tts.wav", max_size=64)
_G711_CACHE: HotCache[str, bytes] = HotCache("tts.g711", max_size=128)


def _cache_voice_key() -> str:
    return f"{resolve_tts_provider()}|{resolve_tts_voice()}|{resolve_tts_rate()}"


def ensure_g711_cached(
    text: str,
    *,
    codec: str = "alaw",
    volume_percent: int = 100,
) -> bool:
    """合成并写入 G.711 热缓存；已命中则直接 True。供入队后后台预热。"""
    text = (text or "").strip()
    if not text:
        return False
    tts = LocalTTS()
    data = tts.synthesize_g711_sync(
        text, codec=codec, volume_percent=volume_percent
    )
    return bool(data)


def warmup_tts_phrases(
    phrases: list[str],
    *,
    codecs: tuple[str, ...] = ("alaw", "ulaw"),
    volumes: tuple[int, ...] = (100, 80),
) -> dict:
    """同步预热：合成 WAV，并按常见 codec/音量转好 G.711。供启动任务调用。"""
    from app.services.hikvision_audio import normalize_g711_codec, wav_bytes_to_g711_8k

    tts = LocalTTS()
    ok = 0
    fail = 0
    seen: set[str] = set()
    vols = tuple(sorted({max(0, min(150, int(v))) for v in volumes})) or (100, 80)
    voice_key = _cache_voice_key()
    for raw in phrases:
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        wav = tts.synthesize_wav_sync(text)
        if not wav:
            fail += 1
            continue
        ok += 1
        for codec in codecs:
            c = normalize_g711_codec(codec)
            for vol in vols:
                key = f"{voice_key}|{c}|{vol}|{text}"
                if _G711_CACHE.get(key) is not None:
                    continue
                try:
                    _G711_CACHE.put(
                        key,
                        wav_bytes_to_g711_8k(wav, codec=c, volume_percent=vol),
                    )
                except Exception:
                    fail += 1
    return {
        "wav_ok": ok,
        "fail": fail,
        "g711": len(_G711_CACHE),
        "phrases": len(seen),
        "volumes": list(vols),
        "voice": voice_key,
    }


class LocalTTS(TTSProvider):
    """TTS：edge=神经女声（Ubuntu 推荐）；local=Windows SAPI / Linux espeak。"""

    async def speak(self, text: str, *, camera_id: int | None = None) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._speak_sync, text)

    def _speak_sync(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if resolve_tts_provider() == "edge":
            wav = self.synthesize_wav_sync(text)
            if wav and self._play_wav_bytes(wav):
                return
        if platform.system() == "Windows":
            if self._speak_win32com(text):
                return
            if self._speak_system_speech(text):
                return
            logger.warning("TTS failed: Windows SAPI 均不可用 text=%s", text[:50])
            return
        try:
            subprocess.run(["espeak", "-v", "zh", text], check=False, timeout=30)
        except Exception as e:
            logger.warning("TTS failed: %s - %s", text[:50], e)

    def _play_wav_bytes(self, wav: bytes) -> bool:
        """把合成好的 WAV 播到本机（Ubuntu aplay / Windows 仅作 edge 回退路径）。"""
        if not wav or len(wav) <= 44:
            return False
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
            tmp.write(wav)
        try:
            if platform.system() == "Windows":
                # winsound 同步播放
                try:
                    import winsound

                    winsound.PlaySound(path, winsound.SND_FILENAME)
                    return True
                except Exception as e:
                    logger.warning("winsound 播放失败: %s", e)
                    return False
            player = shutil.which("aplay") or shutil.which("paplay")
            if not player:
                return False
            r = subprocess.run(
                [player, path],
                check=False,
                timeout=120,
                capture_output=True,
            )
            return r.returncode == 0
        except Exception as e:
            logger.warning("WAV 本机播放失败: %s", e)
            return False
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass

    def _speak_win32com(self, text: str) -> bool:
        try:
            if not _ensure_pywin32():
                raise ImportError("pywintypes")
            import win32com.client  # type: ignore

            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            self._apply_sapi_voice(speaker)
            speaker.Speak(text)
            return True
        except Exception as e:
            logger.warning("win32com TTS 不可用，尝试 System.Speech: %s", e)
            return False

    def _speak_system_speech(self, text: str) -> bool:
        """不依赖 pywin32：用 PowerShell + System.Speech 本机播报。"""
        try:
            voice_hint = self._sapi_select_ps_snippet()
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.SetOutputToDefaultAudioDevice(); "
                "$s.Volume = 100; "
                f"{voice_hint}"
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
        """合成 WAV 字节，供摄像头推流；失败返回空。短句带进程内缓存。"""
        text = sanitize_tts_speech_text(text)
        if not text:
            return b""
        cache_key = f"{_cache_voice_key()}|{text}"
        hit = _WAV_CACHE.get(cache_key)
        if hit:
            return hit
        try:
            wav = b""
            if resolve_tts_provider() == "edge":
                wav = self._edge_tts_to_wav(text)
            if not wav:
                if platform.system() == "Windows":
                    # win32com 写文件通常明显快于每次拉起 PowerShell
                    wav = self._sapi_to_wav_win32com(text)
                    if not wav:
                        wav = self._sapi_to_wav_system_speech(text)
                else:
                    wav = self._espeak_to_wav(text)
            if wav:
                _WAV_CACHE.put(cache_key, wav)
            return wav or b""
        except Exception as e:
            logger.warning("TTS synthesize failed: %s - %s", text[:50], e)
            return b""

    def synthesize_g711_sync(
        self,
        text: str,
        *,
        codec: str = "alaw",
        volume_percent: int = 100,
    ) -> bytes:
        """文本 → G.711（命中启动预热缓存则跳过合成+转码）。"""
        from app.services.hikvision_audio import normalize_g711_codec, wav_bytes_to_g711_8k

        text = sanitize_tts_speech_text(text)
        if not text:
            return b""
        c = normalize_g711_codec(codec)
        try:
            vol = int(volume_percent)
        except (TypeError, ValueError):
            vol = 100
        vol = max(0, min(150, vol))
        key = f"{_cache_voice_key()}|{c}|{vol}|{text}"
        hit = _G711_CACHE.get(key)
        if hit:
            return hit
        wav = self.synthesize_wav_sync(text)
        if not wav:
            return b""
        g711 = wav_bytes_to_g711_8k(wav, codec=c, volume_percent=vol)
        _G711_CACHE.put(key, g711)
        return g711

    def _edge_tts_to_wav(self, text: str) -> bytes:
        """edge-tts → mp3 → ffmpeg WAV。生产 Ubuntu 需出网与 ffmpeg。"""
        voice = resolve_tts_voice()
        rate = resolve_tts_rate()
        try:
            import edge_tts  # type: ignore
        except ImportError:
            logger.warning("edge-tts 未安装，回退本机 TTS；pip install edge-tts")
            return b""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("未找到 ffmpeg，edge-tts 无法转 WAV；Ubuntu: apt install ffmpeg")
            return b""

        mp3_path = ""
        wav_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                mp3_path = tmp.name
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name

            async def _save() -> None:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(mp3_path)

            try:
                asyncio.run(_save())
            except RuntimeError:
                # 已有事件循环的线程里用新 loop
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_save())
                finally:
                    loop.close()

            r = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    mp3_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    wav_path,
                ],
                check=False,
                timeout=90,
                capture_output=True,
            )
            if r.returncode != 0:
                logger.warning(
                    "ffmpeg 转 WAV 失败 code=%s err=%s",
                    r.returncode,
                    (r.stderr or b"")[:200],
                )
                return b""
            data = Path(wav_path).read_bytes()
            return data if len(data) > 44 else b""
        except Exception as e:
            logger.warning("edge-tts 合成失败 voice=%s: %s", voice, e)
            return b""
        finally:
            for p in (mp3_path, wav_path):
                if not p:
                    continue
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

    def _apply_sapi_voice(self, speaker) -> None:
        """按 TTS_VOICE 别名优先选女声/男声；找不到则保持系统默认。"""
        try:
            raw = ""
            try:
                from app.core.config import get_settings

                raw = (get_settings().tts_voice or "").strip()
            except Exception:
                raw = ""
            hints = _SAPI_VOICE_HINTS.get(raw.lower()) or _SAPI_VOICE_HINTS.get(raw)
            if not hints and raw:
                # 直接用配置串当描述子串，如 Huihui
                hints = (raw.lower(),)
            if not hints:
                hints = _SAPI_VOICE_HINTS["gentle_female"]
            voices = speaker.GetVoices()
            for i in range(int(voices.Count)):
                v = voices.Item(i)
                desc = (v.GetDescription() or "").lower()
                gender = ""
                try:
                    gender = (v.GetAttribute("Gender") or "").lower()
                except Exception:
                    pass
                blob = f"{desc} {gender}"
                if any(h in blob for h in hints):
                    speaker.Voice = v
                    return
            # 别名未命中时：女声优先
            if any(k in (raw or "gentle_female").lower() for k in ("female", "女", "huihui", "xiao")) or not raw:
                for i in range(int(voices.Count)):
                    v = voices.Item(i)
                    try:
                        if (v.GetAttribute("Gender") or "").lower() == "female":
                            speaker.Voice = v
                            return
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("SAPI 选音色跳过: %s", e)

    def _sapi_select_ps_snippet(self) -> str:
        """System.Speech 选女声/男声的 PowerShell 片段。"""
        raw = ""
        try:
            from app.core.config import get_settings

            raw = (get_settings().tts_voice or "").strip()
        except Exception:
            raw = ""
        prefer_female = True
        low = raw.lower()
        if low in ("male", "男生", "yunjian") or "male" in low or "男" in raw:
            prefer_female = False
        if prefer_female:
            return (
                "$f = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Female' "
                "-and $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1; "
                "if (-not $f) { $f = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Female' } "
                "| Select-Object -First 1 }; "
                "if ($f) { $s.SelectVoice($f.VoiceInfo.Name) }; "
            )
        return (
            "$m = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Male' "
            "-and $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1; "
            "if ($m) { $s.SelectVoice($m.VoiceInfo.Name) }; "
        )

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
            self._apply_sapi_voice(speaker)
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
            voice_hint = self._sapi_select_ps_snippet()
            cmd = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{out}'); "
                f"{voice_hint}"
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
        should_abort=None,
    ) -> str:
        """播报路由。返回 ok | cancelled | failed | local。

        - force_local：只走电脑扬声器（试听电脑）
        - force_camera：只走摄像头对讲，失败不回退本机（试听摄像头）
        - also_local：摄像头成功后仍再播本机（旧 both 试听）
        - should_abort：摄像头推流可中断（会员入队打断陌生迎宾）
        """
        if force_local:
            await self.local.speak(text, camera_id=camera_id)
            return "local"

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
                if not use_camera:
                    logger.warning(
                        "摄像头播报开关关闭，迎宾/访客改本机扬声器 camera_id=%s "
                        "（请在后台「业务参数 → 摄像头播放迎宾语音」开启）",
                        camera_id,
                    )

        camera_result = "skipped"
        if use_camera:
            try:
                camera_result = await self._play_on_camera(
                    text, int(camera_id), should_abort=should_abort
                )
                if camera_result == "failed":
                    logger.warning("摄像头播报未成功 camera_id=%s", camera_id)
                elif camera_result == "cancelled":
                    logger.info(
                        "摄像头播报已取消（不回退本机、不记成功）camera_id=%s",
                        camera_id,
                    )
            except Exception:
                logger.exception("摄像头播报异常 camera_id=%s", camera_id)
                camera_result = "failed"

        if force_camera:
            if camera_result == "cancelled":
                return "cancelled"
            if camera_result != "ok":
                raise RuntimeError("摄像头播报失败（对讲未成功）")
            return "ok"

        # 取消（如会员打断陌生迎宾）不得回退本机扬声器
        if camera_result == "cancelled":
            return "cancelled"

        camera_ok = camera_result == "ok"
        # 正常迎宾：摄像头失败才回退本机；also_local 时叠播本机
        if also_local or not camera_ok:
            await self.local.speak(text, camera_id=camera_id)
            return "local" if not camera_ok else "ok"
        return "ok"

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

    async def _play_on_camera(
        self, text: str, camera_id: int, *, should_abort=None
    ) -> str:
        """返回 ok | cancelled | failed。cancelled 表示主动打断，不得当成功或回退本机。"""
        from app.core.config import get_settings
        from app.core.database import AsyncSessionLocal
        from app.models import Camera
        from app.services.camera_credentials import build_connect_source, decrypt_password
        from app.services.hikvision_audio import (
            HikvisionTwoWayAudio,
            normalize_g711_codec,
            resolve_isapi_base,
        )
        from app.services.system_settings_service import SystemSettingsService

        async with AsyncSessionLocal() as db:
            cam = await db.get(Camera, camera_id)
            if not cam:
                logger.warning("摄像头不存在 id=%s", camera_id)
                return "failed"
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
            return "failed"

        base = await asyncio.to_thread(resolve_isapi_base, source_url)
        if not base:
            logger.warning("无法解析摄像头 ISAPI 地址 id=%s url=%s", camera_id, source_url)
            return "failed"

        user = (username or "admin").strip() or "admin"
        password = ""
        if password_encrypted:
            try:
                password = decrypt_password(password_encrypted)
            except Exception:
                logger.warning("摄像头密码解密失败 id=%s", camera_id)
                return "failed"
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
            return "failed"

        import time

        def _aborted() -> bool:
            try:
                return bool(should_abort and should_abort())
            except Exception:
                return False

        if _aborted():
            return "cancelled"

        async def _await_with_abort(awaitable, *, poll: float = 0.2):
            """短周期可打断等待：合成/codec 中途 bump 或停采立即 cancelled。"""
            task = asyncio.ensure_future(awaitable)
            while not task.done():
                if _aborted():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return None
                done, _ = await asyncio.wait({task}, timeout=poll)
                if done:
                    break
            if _aborted():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return None
            return task.result()

        client = await asyncio.to_thread(
            HikvisionTwoWayAudio, base, user, password, timeout=30.0
        )
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()

        async def _set_hardware_volume() -> bool:
            # 设备适配器/测试替身可能不提供硬件音量能力；缺失时安全回退软件增益。
            setter = getattr(client, "set_speaker_volume", None)
            if not callable(setter):
                return False
            try:
                return bool(await setter(int(volume)))
            except Exception:
                logger.warning("设置摄像头硬件音量失败，回退软件增益 camera_id=%s", camera_id, exc_info=True)
                return False

        # 硬件 speakerVolume 与 codec 探测并行；PCM 默认按 100% 合成（音量交给设备）
        vol_task = asyncio.create_task(_set_hardware_volume())
        codec_task = asyncio.create_task(client.resolve_codec(block=False))
        g711_alaw_task = loop.run_in_executor(
            None,
            lambda: self.local.synthesize_g711_sync(
                text, codec="alaw", volume_percent=100
            ),
        )
        hw_vol_ok = await _await_with_abort(vol_task)
        if hw_vol_ok is None and _aborted():
            return "cancelled"
        codec = await _await_with_abort(codec_task)
        if codec is None and _aborted():
            return "cancelled"
        codec_ms = int((time.perf_counter() - t0) * 1000)
        if _aborted():
            return "cancelled"
        # 无硬件音量字段时回退软件增益；有硬件则 PCM 保持原音避免双重衰减
        pcm_vol = 100 if hw_vol_ok else int(volume)
        volume_mode = "hardware" if hw_vol_ok else "software"
        t1 = time.perf_counter()
        g711 = await _await_with_abort(g711_alaw_task)
        if g711 is None and _aborted():
            return "cancelled"
        if _aborted():
            return "cancelled"
        need_resynth = (pcm_vol != 100) or (normalize_g711_codec(codec) != "alaw")
        if need_resynth:
            g711 = await _await_with_abort(
                loop.run_in_executor(
                    None,
                    lambda: self.local.synthesize_g711_sync(
                        text, codec=codec, volume_percent=int(pcm_vol)
                    ),
                )
            )
            if g711 is None and _aborted():
                return "cancelled"
            if _aborted():
                return "cancelled"
        synth_ms = int((time.perf_counter() - t1) * 1000)
        if not g711:
            logger.warning("TTS/G711 为空，无法推摄像头 camera_id=%s", camera_id)
            return "failed"
        if synth_ms >= 200:
            logger.info(
                "摄像头播报合成并已缓存 camera_id=%s synth_ms=%s text=%s",
                camera_id,
                synth_ms,
                text[:40],
            )
        if _aborted():
            return "cancelled"
        t2 = time.perf_counter()
        play_result = await client.play_g711(
            g711, codec=codec, should_abort=should_abort
        )
        push_ms = int((time.perf_counter() - t2) * 1000)
        status = str((play_result or {}).get("status") or "failed")
        if status == "cancelled":
            logger.info(
                "摄像头迎宾播报取消 camera_id=%s phase=%s push_ms=%s",
                camera_id,
                (play_result or {}).get("phase"),
                push_ms,
            )
            return "cancelled"
        if status != "completed":
            logger.warning(
                "摄像头迎宾播报失败 camera_id=%s error=%s detail=%s",
                camera_id,
                (play_result or {}).get("error"),
                (play_result or {}).get("detail"),
            )
            return "failed"
        logger.info(
            "摄像头迎宾推流完成(设备出声未验证) camera_id=%s codec=%s volume=%s%% "
            "volume_mode=%s bytes=%s codec_ms=%s synth_ms=%s push_ms=%s total_ms=%s "
            "device_playback=%s",
            camera_id,
            codec,
            volume,
            volume_mode,
            len(g711),
            codec_ms,
            synth_ms,
            push_ms,
            codec_ms + synth_ms + push_ms,
            (play_result or {}).get("device_playback"),
        )
        return "ok"


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
                camera_id = item.get("camera_id")
                kind = item.get("kind") or "member"
                task_id = str(item.get("task_id") or "").strip() or None
                try:
                    camera_id = int(camera_id) if camera_id is not None else None
                except (TypeError, ValueError):
                    camera_id = None
                from app.services.face_service import WelcomeService

                if not str(text).strip():
                    await WelcomeService.set_speak_task_status(
                        task_id,
                        "cancelled",
                        reason="empty_text",
                        kind=kind,
                        camera_id=camera_id,
                    )
                    continue
                # Leader 上该机位已无采集线程：丢弃须走摄像头的待播项（勿拦 test_speak_pc；
                # test_speak=both 仍可继续，speak() 内摄像头失败可叠本机）
                if camera_id is not None and kind in (
                    "member",
                    "unknown_visitor",
                    "test_speak_camera",
                ):
                    try:
                        from app.core.leader_lock import is_leader_held
                        from camera.camera_manager import CameraManager

                        if is_leader_held() and CameraManager().get_worker(camera_id) is None:
                            await WelcomeService.set_speak_task_status(
                                task_id,
                                "cancelled",
                                reason="camera_stopped",
                                kind=kind,
                                camera_id=camera_id,
                            )
                            logger.info(
                                "丢弃停采后迎宾 kind=%s camera_id=%s task_id=%s",
                                kind,
                                camera_id,
                                (task_id or "")[:12],
                            )
                            continue
                    except Exception:
                        logger.debug(
                            "camera_stopped check failed camera_id=%s",
                            camera_id,
                            exc_info=True,
                        )
                logger.info(
                    "TTS 开始播报 kind=%s camera_id=%s task_id=%s text=%s",
                    kind,
                    camera_id,
                    (task_id or "")[:12],
                    text[:40],
                )
                await WelcomeService.set_speak_task_status(
                    task_id,
                    "sending",
                    kind=kind,
                    camera_id=camera_id,
                )
                should_abort = None
                stop_gen_poll: asyncio.Event | None = None
                final_status = "completed"
                cancel_reason: str | None = None
                # abort 时区分 speak_gen_bumped / camera_stopped，勿一律报 bump
                abort_meta: dict[str, str | None] = {"reason": None}
                # 可被同机位后续 bump 打断：陌生/试听/会员长句（新人入队或连点试听）
                # 仅电脑试听不挂 abort
                _abortable = (
                    "unknown_visitor",
                    "test_speak_camera",
                    "test_speak",
                    "member",
                )
                if kind in _abortable and camera_id is not None:
                    from app.core.async_utils import spawn_bg

                    # 先拉 Redis，避免另一 Worker 已 bump 而本进程仍见旧世代
                    gen0 = await WelcomeService.sync_speak_generation_from_redis(
                        camera_id
                    )
                    # bump 失败时停采仍须能打断：Leader 上无 worker 视同 abort
                    # （test_speak both 可叠本机，不因停采整单取消）
                    _stop_aware = kind in (
                        "member",
                        "unknown_visitor",
                        "test_speak_camera",
                    )

                    def _abort_if_bumped() -> bool:
                        if WelcomeService.speak_generation(camera_id) != gen0:
                            abort_meta["reason"] = "speak_gen_bumped"
                            return True
                        if not _stop_aware:
                            return False
                        try:
                            from app.core.leader_lock import is_leader_held
                            from camera.camera_manager import CameraManager

                            if (
                                is_leader_held()
                                and CameraManager().get_worker(camera_id) is None
                            ):
                                abort_meta["reason"] = "camera_stopped"
                                return True
                        except Exception:
                            return False
                        return False

                    should_abort = _abort_if_bumped
                    stop_gen_poll = asyncio.Event()

                    async def _poll_speak_gen() -> None:
                        while not stop_gen_poll.is_set():
                            await WelcomeService.sync_speak_generation_from_redis(
                                camera_id
                            )
                            try:
                                await asyncio.wait_for(
                                    stop_gen_poll.wait(), timeout=0.2
                                )
                            except asyncio.TimeoutError:
                                pass

                    spawn_bg(_poll_speak_gen())
                try:
                    speak_outcome = await self.provider.speak(
                        text,
                        camera_id=camera_id,
                        force_local=(kind == "test_speak_pc"),
                        force_camera=(kind == "test_speak_camera"),
                        also_local=(kind == "test_speak"),
                        should_abort=should_abort,
                    )
                    if speak_outcome == "cancelled":
                        final_status = "cancelled"
                        if kind in _abortable and camera_id is not None:
                            cancel_reason = (
                                abort_meta.get("reason") or "speak_gen_bumped"
                            )
                        else:
                            cancel_reason = "cancelled"
                    elif speak_outcome in ("ok", "local"):
                        final_status = "completed"
                    else:
                        final_status = "failed"
                    await WelcomeService.set_speak_task_status(
                        task_id,
                        final_status,
                        kind=kind,
                        camera_id=camera_id,
                        speak_outcome=speak_outcome,
                        reason=cancel_reason,
                        device_playback=(
                            (
                                "unverified"
                                if speak_outcome == "ok"
                                else "local_only"
                                if speak_outcome == "local"
                                else None
                            )
                            if final_status == "completed"
                            and kind
                            in (
                                "member",
                                "unknown_visitor",
                                "test_speak_camera",
                                "test_speak",
                            )
                            else None
                        ),
                    )
                    final_status = ""
                except Exception as speak_err:
                    logger.error("TTS speak failed: %s", speak_err)
                    await WelcomeService.set_speak_task_status(
                        task_id,
                        "failed",
                        kind=kind,
                        camera_id=camera_id,
                        error=str(speak_err)[:160],
                    )
                    final_status = ""
                finally:
                    if stop_gen_poll is not None:
                        stop_gen_poll.set()
                if final_status:
                    await WelcomeService.set_speak_task_status(
                        task_id,
                        final_status,
                        kind=kind,
                        camera_id=camera_id,
                    )
                logger.info(
                    "TTS 播报结束 kind=%s camera_id=%s task_id=%s",
                    kind,
                    camera_id,
                    (task_id or "")[:12],
                )
            except Exception as e:
                logger.error("TTS worker error: %s", e)

    def stop(self) -> None:
        self._running = False
