import asyncio
import logging
import platform

logger = logging.getLogger(__name__)


class TTSProvider:
    async def speak(self, text: str) -> None:
        raise NotImplementedError


class LocalTTS(TTSProvider):
    """本地 TTS - Windows SAPI / Linux espeak"""

    async def speak(self, text: str) -> None:
        loop = asyncio.get_event_loop()
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


class TTSWorker:
    def __init__(self, provider: TTSProvider | None = None):
        self.provider = provider or LocalTTS()
        self._running = False

    async def run(self, queue: asyncio.Queue) -> None:
        self._running = True
        while self._running:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                await self.provider.speak(item["text"])
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("TTS worker error: %s", e)

    def stop(self) -> None:
        self._running = False
