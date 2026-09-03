"""局域网 ESC/POS 热敏打印（芯烨 XP-80T USB+WiFi 等，TCP 9100）。"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ESC = b"\x1b"
GS = b"\x1d"
_INIT = ESC + b"@"
_ALIGN_LEFT = ESC + b"a\x00"
_ALIGN_CENTER = ESC + b"a\x01"
_BOLD_ON = ESC + b"E\x01"
_BOLD_OFF = ESC + b"E\x00"
_SIZE_NORMAL = GS + b"!\x00"
_SIZE_DOUBLE = GS + b"!\x11"
_CUT_PARTIAL = GS + b"V\x01"
_LF = b"\n"

_TAG_RE = re.compile(
    r"</?(?:C|B|B2|BOLD|BR|CUT|BARCODE(?:\s+[^>]*)?)>",
    re.IGNORECASE,
)
_BARCODE_RE = re.compile(
    r'<BARCODE[^>]*>([^<]+)</BARCODE>',
    re.IGNORECASE,
)


def _encode_text(text: str, encoding: str = "gbk") -> bytes:
    return (text or "").encode(encoding, errors="replace")


def _barcode_code128(data: str) -> bytes:
    payload = (data or "").strip().encode("ascii", errors="ignore")
    if not payload:
        return b""
    n = len(payload)
    if n > 255:
        payload = payload[:255]
        n = len(payload)
    return GS + b"kI" + bytes([n]) + payload + _LF


def xpyun_markup_to_escpos(content: str, *, encoding: str = "gbk") -> bytes:
    """将芯烨云标签小票内容转为 ESC/POS 字节流（XP-80T 等通用）。"""
    buf = bytearray(_INIT)
    for raw_line in (content or "").split("<BR>"):
        line = raw_line.strip()
        if not line:
            buf.extend(_LF)
            continue

        has_cut = "<CUT>" in line.upper()
        line = re.sub(r"<CUT>", "", line, flags=re.IGNORECASE).strip()
        if not line and has_cut:
            buf.extend(_LF * 2)
            buf.extend(_CUT_PARTIAL)
            continue

        centered = bool(re.search(r"<C>", line, re.IGNORECASE))
        double = bool(re.search(r"<B2>", line, re.IGNORECASE))
        bold = bool(re.search(r"<B(?:OLD)?>", line, re.IGNORECASE))

        barcode_match = _BARCODE_RE.search(line)
        if barcode_match:
            before = line[: barcode_match.start()]
            after = line[barcode_match.end() :]
            if before.strip():
                buf.extend(_render_text_line(before, centered, bold, double, encoding))
            buf.extend(_ALIGN_CENTER)
            buf.extend(_barcode_code128(barcode_match.group(1)))
            if after.strip():
                buf.extend(_render_text_line(after, centered, bold, double, encoding))
        else:
            buf.extend(_render_text_line(line, centered, bold, double, encoding))

        if has_cut:
            buf.extend(_LF * 2)
            buf.extend(_CUT_PARTIAL)

    if not buf.endswith(_CUT_PARTIAL):
        buf.extend(_LF * 3)
        buf.extend(_CUT_PARTIAL)
    return bytes(buf)


def _render_text_line(
    line: str,
    centered: bool,
    bold: bool,
    double: bool,
    encoding: str,
) -> bytes:
    text = _TAG_RE.sub("", line).strip()
    if not text:
        return b""
    out = bytearray()
    out.extend(_ALIGN_CENTER if centered else _ALIGN_LEFT)
    if double:
        out.extend(_SIZE_DOUBLE)
    if bold:
        out.extend(_BOLD_ON)
    out.extend(_encode_text(text, encoding))
    if bold:
        out.extend(_BOLD_OFF)
    if double:
        out.extend(_SIZE_NORMAL)
    out.extend(_LF)
    return bytes(out)


class EscposClient:
    def __init__(self, *, host: str, port: int = 9100, timeout: float = 8.0) -> None:
        self.host = (host or "").strip()
        self.port = int(port)
        self.timeout = float(timeout)

    @property
    def configured(self) -> bool:
        return bool(self.host)

    async def print_ticket(self, content: str, *, copies: int = 1) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("未配置打印机 IP（PRINTER_HOST）")
        payload = xpyun_markup_to_escpos(content)
        copies = max(1, min(int(copies), 5))
        for i in range(copies):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.timeout,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                logger.warning("局域网打印机连接失败 host=%s port=%s err=%s", self.host, self.port, exc)
                raise RuntimeError(f"无法连接打印机 {self.host}:{self.port}") from exc
            try:
                writer.write(payload)
                await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            if i < copies - 1:
                await asyncio.sleep(0.3)
        return {"code": 0, "msg": "ok", "mode": "local", "host": self.host}
